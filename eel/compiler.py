"""The compilation pipeline: source → IR → checks → forms → .beam."""

from __future__ import annotations

import atexit
import base64
import hashlib
import json
import os
import pathlib
import socket
import subprocess
import time

from . import backend, checks, etf, frontend
from .errors import EelError
from .term import Atom, term_to_erlang

ESCRIPT = pathlib.Path(__file__).parent / "eel_compile.escript"

#: The "client ↔ escript" protocol version; on a mismatch the daemon is restarted.
PROTOCOL = 2

#: The build-artefact format version: bump it and the old manifest is invalidated.
BUILD_FORMAT = 1

#: Sources that affect the build result: their hash invalidates the manifest.
_BUILDER_SOURCES = (
    "compiler.py",
    "frontend.py",
    "backend.py",
    "checks.py",
    "ir.py",
    "etf.py",
    "term.py",
    "eel_compile.escript",
)

_FINGERPRINT: str | None = None

_CACHE = pathlib.Path(
    os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache"
) / "eel"


def compile_source(source: str, path: str, name: str) -> list:
    """A source → Erlang forms (Python values)."""
    try:
        module = frontend.parse_module(source, path, name)
        checks.check(module)
        return backend.module_forms(module)
    except EelError as e:
        msg = str(e)
        if not msg.startswith(f"{path}:"):
            raise EelError(f"{path}:{msg}") from e
        raise


def module_name(relative_path: pathlib.PurePath) -> str:
    """``src/a/b/c.eel`` → ``a@b@c``; ``src/hello.eel`` → ``hello``."""
    return "@".join(relative_path.with_suffix("").parts)


def builder_fingerprint() -> str:
    """A hash of the compiler sources: change code generation and everything is rebuilt."""
    global _FINGERPRINT
    if _FINGERPRINT is None:
        digest = hashlib.sha256()
        package = pathlib.Path(__file__).parent
        for name in _BUILDER_SOURCES:
            digest.update((package / name).read_bytes())
        _FINGERPRINT = digest.hexdigest()
    return _FINGERPRINT


def _load_manifest(path: pathlib.Path, builder: str) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if data.get("format") != BUILD_FORMAT or data.get("builder") != builder:
        return {}
    modules = data.get("modules")
    return modules if isinstance(modules, dict) else {}


def _save_manifest(path: pathlib.Path, builder: str, modules: dict) -> None:
    payload = {"format": BUILD_FORMAT, "builder": builder, "modules": dict(sorted(modules.items()))}
    path.write_text(json.dumps(payload, indent=1) + "\n")


def _install_key() -> str:
    """A key by escript path: different eel installations get different daemons."""
    return hashlib.sha1(str(ESCRIPT.resolve()).encode("utf-8")).hexdigest()[:12]


def daemon_socket() -> pathlib.Path:
    return _CACHE / f"compiler-{_install_key()}.sock"


def daemon_pid_file() -> pathlib.Path:
    return _CACHE / f"compiler-{_install_key()}.pid"


def daemon_log() -> pathlib.Path:
    return _CACHE / f"compiler-{_install_key()}.log"


class DaemonUnavailable(RuntimeError):
    """The daemon is unreachable — we may silently fall back to an in-process escript."""


class CompilerDaemon:
    """A background escript on a unix socket: it lives until asked to stop.

    The first `build` starts it, later commands (from other processes too)
    reuse the Erlang VM already running.
    """

    def __init__(self) -> None:
        self.timeout = 5.0
        self._proc: subprocess.Popen | None = None

    # --- transport ---

    def _connect(self) -> socket.socket:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(str(daemon_socket()))
        except OSError as exc:
            sock.close()
            raise DaemonUnavailable(str(exc)) from exc
        return sock

    def _request(self, term) -> str:
        """Send a request and return the reply line (``OK …`` / ``ERR …``)."""
        line = term_to_erlang(term) + ".\n"
        try:
            sock = self._connect()
        except DaemonUnavailable:
            raise
        try:
            sock.sendall(line.encode("utf-8"))
            chunks = []
            while not chunks or not chunks[-1].endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    raise DaemonUnavailable("compiler daemon closed the connection")
                chunks.append(chunk)
        except OSError as exc:
            raise DaemonUnavailable(str(exc)) from exc
        finally:
            sock.close()
        return b"".join(chunks).decode("utf-8").rstrip("\n")

    @staticmethod
    def _payload(reply: str) -> str:
        return base64.b64decode(reply.split(" ", 1)[1]).decode("utf-8").strip()

    # --- control ---

    def ping(self) -> dict | None:
        """``{"version": "2", "compiled": "5"}``, or None when the daemon does not answer."""
        try:
            reply = self._request((Atom("ping"),))
        except DaemonUnavailable:
            return None
        if not reply.startswith("OK"):
            return None
        info = {}
        for chunk in self._payload(reply).split():
            key, _, value = chunk.partition("=")
            info[key] = value
        return info

    def start(self) -> dict:
        """Start the daemon and wait until it is ready."""
        daemon_socket().parent.mkdir(parents=True, exist_ok=True)
        for stale in (daemon_socket(), daemon_pid_file()):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass
        with open(daemon_log(), "ab") as log:
            # stdin is a pipe: when the parent dies it closes, and the escript exits by itself
            self._proc = subprocess.Popen(
                ["escript", str(ESCRIPT), "--daemon", str(daemon_socket()), str(daemon_pid_file())],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=log,
                start_new_session=True,
                close_fds=True,
            )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            info = self.ping()
            if info is not None:
                return info
            time.sleep(0.02)
        raise DaemonUnavailable(
            f"compiler daemon did not start (see {daemon_log()})"
        )

    def ensure(self) -> dict:
        """A live daemon of the right version — or start a new one."""
        info = self.ping()
        if info is not None and info.get("version") == str(PROTOCOL):
            return info
        if info is not None:  # a foreign protocol version — restart it
            self.stop()
        return self.start()

    def stop(self) -> bool:
        """Ask the daemon to exit; remove the socket and the pid file."""
        stopped = False
        try:
            stopped = self._request((Atom("stop"),)) == "OK"
        except DaemonUnavailable:
            pass
        if self._proc is not None:
            try:
                self._proc.stdin.close()      # close the pipe: the escript sees the parent is gone
            except OSError:
                pass
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        for path in (daemon_socket(), daemon_pid_file()):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        return stopped

    def compile(self, forms_path: pathlib.Path, erl: bool, *, autostart: bool = False) -> None:
        """Compile a blob through the daemon.

        ``autostart=False`` — the daemon is not started: it lives only as a child of the build
        server (the lifetimes are tied); without it the caller builds in-process.
        """
        if self.ping() is None:
            if not autostart:
                raise DaemonUnavailable("compiler daemon is not running")
            self.ensure()
        reply = self._request(
            (Atom("compile"), str(forms_path), Atom("true") if erl else Atom("false"))
        )
        if reply == "OK":
            return
        if reply.startswith("ERR "):
            raise EelError(self._payload(reply))
        raise DaemonUnavailable(f"unexpected compiler reply: {reply!r}")


class CompilerProcess:
    """An escript inside the process: the fallback path and `--no-daemon`."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self.starts = 0

    def _ensure(self) -> subprocess.Popen:
        if self._proc is None or self._proc.poll() is not None:
            self._proc = subprocess.Popen(
                ["escript", str(ESCRIPT)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            self.starts += 1
        return self._proc

    def compile(self, forms_path: pathlib.Path, erl: bool) -> None:
        """Compile an ETF blob of forms; errors are EelError with the escript's text."""
        proc = self._ensure()
        request = term_to_erlang(
            (Atom("compile"), str(forms_path), Atom("true") if erl else Atom("false"))
        )
        try:
            proc.stdin.write(request + ".\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
        except (BrokenPipeError, OSError) as exc:
            raise EelError("compiler process died") from exc
        if not line:
            stderr = proc.stderr.read().strip() if proc.stderr else ""
            raise EelError(stderr or "compiler process exited")
        reply = line.rstrip("\n")
        if reply == "OK":
            return
        if reply.startswith("ERR "):
            raise EelError(CompilerDaemon._payload(reply))
        raise EelError(f"unexpected compiler reply: {reply!r}")

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None


DAEMON = CompilerDaemon()
COMPILER = CompilerProcess()
atexit.register(COMPILER.close)


def _needs_rebuild(entry, digest, ebin, artefacts, name, erl, dump_forms) -> bool:
    """Whether a module must be rebuilt: no entry, another hash, missing artefacts."""
    if entry != digest:
        return True
    if not (ebin / f"{name}.beam").exists():
        return True
    if erl and not (ebin / f"{name}.erl").exists():
        return True
    if dump_forms and not (artefacts / f"{name}.abstr").exists():
        return True
    return False


def build(
    root: str | pathlib.Path,
    *,
    erl: bool = False,
    dump_forms: bool = False,
    daemon: bool = True,
    force: bool = False,
) -> pathlib.Path:
    """Compile ``src/*.eel`` into ``build/dev/erlang/<app>/ebin``.

    ``erl`` — also write ``.erl`` next to each ``.beam``; ``dump_forms`` — write readable
    ``.abstr``; ``daemon`` — use the background daemon (otherwise an in-process escript);
    ``force`` — rebuild everything, ignoring the manifest (otherwise changed modules only).
    """
    root = pathlib.Path(root).resolve()
    src = root / "src"
    app = root.name
    out = root / "build" / "dev" / "erlang" / app
    artefacts = out / "_eel_artefacts"
    ebin = out / "ebin"
    artefacts.mkdir(parents=True, exist_ok=True)
    ebin.mkdir(parents=True, exist_ok=True)

    modules = []
    builder = builder_fingerprint()
    manifest_path = artefacts / "build.json"
    previous = _load_manifest(manifest_path, builder)

    current = {}
    jobs = []
    for path in sorted(src.glob("**/*.eel")):
        rel = path.relative_to(src)
        name = module_name(rel)
        modules.append(name)
        source_path = path.relative_to(root)
        source = path.read_text()
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        current[name] = digest
        if not force and not _needs_rebuild(
            previous.get(name), digest, ebin, artefacts, name, erl, dump_forms
        ):
            continue
        forms = compile_source(source, str(source_path), name)
        jobs.append((name, forms, str(source_path)))
        if dump_forms:
            (artefacts / f"{name}.abstr").write_text(
                "".join(term_to_erlang(form) + ".\n" for form in forms)
            )

    for gone in sorted(set(previous) - set(current)):   # the module was removed or renamed
        for stale in (ebin / f"{gone}.beam", ebin / f"{gone}.erl", artefacts / f"{gone}.abstr"):
            stale.unlink(missing_ok=True)

    if jobs:
        forms_path = artefacts / "forms.etf"
        forms_path.write_bytes(
            etf.encode([(str(ebin / name), forms, source) for name, forms, source in jobs])
        )
        if daemon:
            try:
                DAEMON.compile(forms_path, erl)   # only if the server has already started it
            except DaemonUnavailable:
                COMPILER.compile(forms_path, erl)  # no server — build inside the process
        else:
            COMPILER.compile(forms_path, erl)

    _save_manifest(manifest_path, builder, current)

    app_spec = (
        Atom("application"),
        Atom(app),
        [
            (Atom("vsn"), "0.1.0"),
            (Atom("modules"), [Atom(m) for m in modules]),
            (Atom("registered"), []),
            (Atom("applications"), [Atom("kernel"), Atom("stdlib")]),
        ],
    )
    (ebin / f"{app}.app").write_text(term_to_erlang(app_spec) + ".\n")
    return out
