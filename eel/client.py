"""Тонкий клиент сервера сборки: только сокет, JSON и пути.

Держится намеренно лёгким — этот модуль импортирует CLI до того, как решит, нужен ли вообще
тяжёлый компилятор: сборка в живом сервере стоит ~3 мс, а импорт `eel.compiler` — десятки.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import socket
import sys
import time

from .errors import EelError

#: Версия протокола «клиент ↔ сервер сборки».
SERVER_PROTOCOL = 1

_CACHE = pathlib.Path(
    os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache"
) / "eel"


def install_key() -> str:
    """Ключ по пути установки: у разных копий eel — свои серверы."""
    package = pathlib.Path(__file__).resolve().parent
    return hashlib.sha1(str(package).encode("utf-8")).hexdigest()[:12]


def server_socket() -> pathlib.Path:
    return _CACHE / f"build-{install_key()}.sock"


def server_pid_file() -> pathlib.Path:
    return _CACHE / f"build-{install_key()}.pid"


def server_log() -> pathlib.Path:
    return _CACHE / f"build-{install_key()}.log"


class ServerUnavailable(RuntimeError):
    """Сервер недоступен — вызывающий решает, поднимать ли его или собирать внутри процесса."""


def request(payload: dict, timeout: float = 300.0) -> dict:
    """Отправить запрос серверу (одна JSON-строка) и вернуть ответ."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(server_socket()))
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buffer = b""
        while not buffer.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                raise ServerUnavailable("build server closed the connection")
            buffer += chunk
    except OSError as exc:
        raise ServerUnavailable(str(exc)) from exc
    finally:
        sock.close()
    try:
        return json.loads(buffer.decode("utf-8"))
    except ValueError as exc:
        raise ServerUnavailable(f"bad reply from build server: {buffer!r}") from exc


def ping(timeout: float = 2.0) -> dict | None:
    """``{"version": 1, "builds": 3}`` или None, если сервер не отвечает."""
    try:
        reply = request({"op": "ping"}, timeout=timeout)
    except ServerUnavailable:
        return None
    return reply if reply.get("ok") else None


def _clean_endpoint() -> None:
    for path in (server_socket(), server_pid_file()):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def start() -> dict:
    """Поднять сервер сборки в фоне и дождаться готовности."""
    import subprocess  # тяжёлый импорт — только для того, кто действительно запускает сервер

    server_socket().parent.mkdir(parents=True, exist_ok=True)
    _clean_endpoint()
    with open(server_log(), "ab") as log:
        subprocess.Popen(
            [sys.executable, "-m", "eel.server", str(server_socket()), str(server_pid_file())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        info = ping()
        if info is not None:
            return info
        time.sleep(0.02)
    raise ServerUnavailable(f"build server did not start (see {server_log()})")


def ensure() -> dict:
    """Живой сервер нужной версии — или поднять новый."""
    info = ping()
    if info is not None and info.get("version") == SERVER_PROTOCOL:
        return info
    if info is not None:  # чужая версия протокола
        stop()
    return start()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def stop(wait: float = 5.0) -> bool:
    """Попросить сервер выйти; снять сокет и pid-файл.

    Ждём, пока процесс действительно исчезнет: сервер в `finally` гасит демон компилятора,
    и если вернуть управление раньше, новый демон может быть убит умирающим сервером.
    """
    pid = None
    try:
        pid = int(server_pid_file().read_text().strip())
    except (OSError, ValueError):
        pass
    stopped = False
    try:
        stopped = bool(request({"op": "stop"}, timeout=10.0).get("ok"))
    except ServerUnavailable:
        pass
    if pid is not None:
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and _alive(pid):
            time.sleep(0.02)
    _clean_endpoint()
    return stopped


def build_remote(
    root: str | pathlib.Path,
    *,
    erl: bool = False,
    dump_forms: bool = False,
    force: bool = False,
) -> bool:
    """Собрать через сервер. True — сделано; False — сервер недоступен (собирать внутри процесса).

    Ошибки компиляции поднимаются как `EelError` с текстом от сервера.
    """
    try:
        ensure()
        reply = request(
            {
                "op": "build",
                "root": str(pathlib.Path(root).resolve()),
                "erl": erl,
                "dump_forms": dump_forms,
                "force": force,
            }
        )
    except ServerUnavailable:
        return False
    if reply.get("ok"):
        return True
    raise EelError(str(reply.get("error", "build failed")))
