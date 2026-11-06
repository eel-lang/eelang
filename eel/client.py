"""The thin client of the build server: just a socket, JSON and paths.

Deliberately lightweight — the CLI imports this module before deciding whether the heavy
compiler is needed at all: a build in the live server costs ~3 ms, while importing `eel.compiler` costs dozens.
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

#: The "client ↔ build server" protocol version.
SERVER_PROTOCOL = 1

_CACHE = pathlib.Path(
    os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache"
) / "eel"


def install_key() -> str:
    """A key by install path: different eel copies get their own servers."""
    package = pathlib.Path(__file__).resolve().parent
    return hashlib.sha1(str(package).encode("utf-8")).hexdigest()[:12]


def server_socket() -> pathlib.Path:
    return _CACHE / f"build-{install_key()}.sock"


def server_pid_file() -> pathlib.Path:
    return _CACHE / f"build-{install_key()}.pid"


def server_log() -> pathlib.Path:
    return _CACHE / f"build-{install_key()}.log"


class ServerUnavailable(RuntimeError):
    """The server is unreachable — the caller decides to start it or build in-process."""


def request(payload: dict, timeout: float = 300.0) -> dict:
    """Send one JSON-line request to the server and return the reply."""
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
    """``{"version": 1, "builds": 3}``, or None when the server does not answer."""
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
    """Start the build server in the background and wait until it is ready."""
    import subprocess  # a heavy import, only for whoever actually starts the server

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
    """A live server of the right version — or start a new one."""
    info = ping()
    if info is not None and info.get("version") == SERVER_PROTOCOL:
        return info
    if info is not None:  # a foreign protocol version
        stop()
    return start()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def stop(wait: float = 5.0) -> bool:
    """Ask the server to exit; remove the socket and the pid file.

    We wait for the process to really disappear: the server stops the compiler daemon in its
    `finally`, and returning earlier could let the dying server kill a freshly started daemon.
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
    """Build through the server. True — done; False — no server (build in-process).

    Compile errors are raised as `EelError` with the server's text.
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
