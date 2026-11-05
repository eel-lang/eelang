"""Конвейер компиляции: source → IR → проверки → формы → .beam."""

from __future__ import annotations

import atexit
import base64
import hashlib
import os
import pathlib
import socket
import subprocess
import time

from . import backend, checks, etf, frontend
from .errors import SquealError
from .term import Atom, term_to_erlang

ESCRIPT = pathlib.Path(__file__).parent / "squeal_compile.escript"

#: Версия протокола «клиент ↔ escript»; при расхождении демон перезапускается.
PROTOCOL = 2

_CACHE = pathlib.Path(
    os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache"
) / "squeal"


def compile_source(source: str, path: str, name: str) -> list:
    """Исходник → Erlang-формы (значения Python)."""
    try:
        module = frontend.parse_module(source, path, name)
        checks.check(module)
        return backend.module_forms(module)
    except SquealError as e:
        msg = str(e)
        if not msg.startswith(f"{path}:"):
            raise SquealError(f"{path}:{msg}") from e
        raise


def module_name(relative_path: pathlib.PurePath) -> str:
    """``src/a/b/c.ee`` → ``a@b@c``; ``src/hello.ee`` → ``hello``."""
    return "@".join(relative_path.with_suffix("").parts)


def _install_key() -> str:
    """Ключ по пути escript: у разных установок squeal — разные демоны."""
    return hashlib.sha1(str(ESCRIPT.resolve()).encode("utf-8")).hexdigest()[:12]


def daemon_socket() -> pathlib.Path:
    return _CACHE / f"compiler-{_install_key()}.sock"


def daemon_pid_file() -> pathlib.Path:
    return _CACHE / f"compiler-{_install_key()}.pid"


def daemon_log() -> pathlib.Path:
    return _CACHE / f"compiler-{_install_key()}.log"


class DaemonUnavailable(RuntimeError):
    """Демон недоступен — можно тихо откатиться к escript внутри процесса."""


class CompilerDaemon:
    """Фоновый escript на unix-сокете: живёт, пока не попросят остановиться.

    Первый `build` поднимает его, дальнейшие команды (в том числе из других процессов)
    переиспользуют уже поднятую Erlang-VM.
    """

    def __init__(self) -> None:
        self.timeout = 5.0

    # --- транспорт ---

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
        """Отправить запрос, вернуть строку ответа (``OK …`` / ``ERR …``)."""
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

    # --- управление ---

    def ping(self) -> dict | None:
        """``{"version": "2", "compiled": "5"}`` или None, если демон не отвечает."""
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
        """Поднять демон и дождаться готовности."""
        daemon_socket().parent.mkdir(parents=True, exist_ok=True)
        for stale in (daemon_socket(), daemon_pid_file()):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass
        with open(daemon_log(), "ab") as log:
            subprocess.Popen(
                ["escript", str(ESCRIPT), "--daemon", str(daemon_socket()), str(daemon_pid_file())],
                stdin=subprocess.DEVNULL,
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
        """Живой демон нужной версии — или поднять новый."""
        info = self.ping()
        if info is not None and info.get("version") == str(PROTOCOL):
            return info
        if info is not None:  # чужая версия протокола — перезапустить
            self.stop()
        return self.start()

    def stop(self) -> bool:
        """Попросить демон выйти; снять сокет и pid-файл."""
        stopped = False
        try:
            stopped = self._request((Atom("stop"),)) == "OK"
        except DaemonUnavailable:
            pass
        for path in (daemon_socket(), daemon_pid_file()):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        return stopped

    def compile(self, forms_path: pathlib.Path, erl: bool) -> None:
        self.ensure()
        reply = self._request(
            (Atom("compile"), str(forms_path), Atom("true") if erl else Atom("false"))
        )
        if reply == "OK":
            return
        if reply.startswith("ERR "):
            raise SquealError(self._payload(reply))
        raise DaemonUnavailable(f"unexpected compiler reply: {reply!r}")


class CompilerProcess:
    """escript внутри процесса: запасной путь и `--no-daemon`."""

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
        """Скомпилировать ETF-блоб с формами; ошибки — SquealError с текстом от escript."""
        proc = self._ensure()
        request = term_to_erlang(
            (Atom("compile"), str(forms_path), Atom("true") if erl else Atom("false"))
        )
        try:
            proc.stdin.write(request + ".\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
        except (BrokenPipeError, OSError) as exc:
            raise SquealError("compiler process died") from exc
        if not line:
            stderr = proc.stderr.read().strip() if proc.stderr else ""
            raise SquealError(stderr or "compiler process exited")
        reply = line.rstrip("\n")
        if reply == "OK":
            return
        if reply.startswith("ERR "):
            raise SquealError(CompilerDaemon._payload(reply))
        raise SquealError(f"unexpected compiler reply: {reply!r}")

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


def build(
    root: str | pathlib.Path,
    *,
    erl: bool = False,
    dump_forms: bool = False,
    daemon: bool = True,
) -> pathlib.Path:
    """Компилирует ``src/*.ee`` в ``build/dev/erlang/<app>/ebin``.

    ``erl`` — дополнительно писать ``.erl`` рядом с ``.beam``; ``dump_forms`` — писать читаемые
    ``.abstr``; ``daemon`` — использовать фоновый демон (иначе escript внутри процесса).
    """
    root = pathlib.Path(root).resolve()
    src = root / "src"
    app = root.name
    out = root / "build" / "dev" / "erlang" / app
    artefacts = out / "_squeal_artefacts"
    ebin = out / "ebin"
    artefacts.mkdir(parents=True, exist_ok=True)
    ebin.mkdir(parents=True, exist_ok=True)

    modules = []
    jobs = []
    for path in sorted(src.glob("**/*.ee")):
        rel = path.relative_to(src)
        name = module_name(rel)
        modules.append(name)
        source_path = path.relative_to(root)
        forms = compile_source(path.read_text(), str(source_path), name)
        jobs.append((name, forms, str(source_path)))
        if dump_forms:
            (artefacts / f"{name}.abstr").write_text(
                "".join(term_to_erlang(form) + ".\n" for form in forms)
            )

    if jobs:
        forms_path = artefacts / "forms.etf"
        forms_path.write_bytes(
            etf.encode([(str(ebin / name), forms, source) for name, forms, source in jobs])
        )
        if daemon:
            try:
                DAEMON.compile(forms_path, erl)
            except DaemonUnavailable:
                COMPILER.compile(forms_path, erl)  # не смогли поднять демон — не падаем
        else:
            COMPILER.compile(forms_path, erl)

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
