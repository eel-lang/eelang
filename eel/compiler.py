"""Конвейер компиляции: source → IR → проверки → формы → .beam."""

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

#: Версия протокола «клиент ↔ escript»; при расхождении демон перезапускается.
PROTOCOL = 2

#: Версия формата артефактов сборки: меняем — прежний манифест считается недействительным.
BUILD_FORMAT = 1

#: Исходники, влияющие на результат сборки: их хэш инвалидирует манифест.
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
    """Исходник → Erlang-формы (значения Python)."""
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
    """Хэш исходников компилятора: изменили кодогенерацию — пересобираем всё."""
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
    """Ключ по пути escript: у разных установок eel — разные демоны."""
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
        self._proc: subprocess.Popen | None = None

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
            # stdin — труба: когда родитель умирает, она закрывается, и escript выходит сам
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
        if self._proc is not None:
            try:
                self._proc.stdin.close()      # закрыть трубу: escript поймёт, что родитель ушёл
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
        """Скомпилировать блоб через демон.

        ``autostart=False`` — демон не поднимаем: он живёт только как ребёнок сервера сборки
        (жизненные циклы связаны), а без него вызывающий собирает внутри процесса.
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
        """Скомпилировать ETF-блоб с формами; ошибки — EelError с текстом от escript."""
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
    """Пересобирать ли модуль: нет записи, другой хэш, пропали нужные артефакты."""
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
    """Компилирует ``src/*.eel`` в ``build/dev/erlang/<app>/ebin``.

    ``erl`` — дополнительно писать ``.erl`` рядом с ``.beam``; ``dump_forms`` — писать читаемые
    ``.abstr``; ``daemon`` — использовать фоновый демон (иначе escript внутри процесса);
    ``force`` — пересобрать всё, игнорируя манифест (иначе только изменившиеся модули).
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

    for gone in sorted(set(previous) - set(current)):   # модуль удалён или переименован
        for stale in (ebin / f"{gone}.beam", ebin / f"{gone}.erl", artefacts / f"{gone}.abstr"):
            stale.unlink(missing_ok=True)

    if jobs:
        forms_path = artefacts / "forms.etf"
        forms_path.write_bytes(
            etf.encode([(str(ebin / name), forms, source) for name, forms, source in jobs])
        )
        if daemon:
            try:
                DAEMON.compile(forms_path, erl)   # только если демон уже поднят сервером
            except DaemonUnavailable:
                COMPILER.compile(forms_path, erl)  # сервера нет — собираем внутри процесса
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
