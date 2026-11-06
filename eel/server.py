"""The build server: a live Python process that builds a project on request.

It starts in the background (`python -m eel.server <sock> <pid>`) and accepts, over a unix
socket, one JSON line per request:

    {"op": "build", "root": "...", "erl": false, "dump_forms": false, "force": false}
    {"op": "ping"}
    {"op": "stop"}

The reply is one JSON line as well: ``{"ok": true, ...}`` or ``{"ok": false, "error": "..."}``.
The server does not watch the filesystem: a build happens only on request, but it does not pay
for interpreter startup or the compiler import (see `eel/client.py`).
"""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import traceback

from . import client
from .compiler import DAEMON, build
from .errors import EelError

STATE = {"builds": 0, "errors": 0}


def _handle(payload: dict) -> tuple[dict, bool]:
    """Handle one request: (the reply, whether to keep running)."""
    op = payload.get("op")
    try:
        if op == "build":
            build(
                str(payload["root"]),
                erl=bool(payload.get("erl")),
                dump_forms=bool(payload.get("dump_forms")),
                force=bool(payload.get("force")),
            )
            STATE["builds"] += 1
            return {"ok": True}, True
        if op == "ping":
            return {
                "ok": True,
                "version": client.SERVER_PROTOCOL,
                "builds": STATE["builds"],
                "errors": STATE["errors"],
            }, True
        if op == "stop":
            return {"ok": True}, False
        return {"ok": False, "error": f"bad request: {op!r}"}, True
    except EelError as exc:
        STATE["errors"] += 1
        return {"ok": False, "error": str(exc)}, True
    except Exception:  # noqa: BLE001 — one bad request must not bring the server down
        STATE["errors"] += 1
        traceback.print_exc()
        return {"ok": False, "error": "internal build server error (see log)"}, True


def _serve(conn: socket.socket) -> bool:
    """Serve one connection: True — keep running, False — stop."""
    with conn:
        buffer = b""
        while True:
            try:
                chunk = conn.recv(65536)
            except OSError:
                return True
            if not chunk:
                return True
            buffer += chunk
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                try:
                    payload = json.loads(line.decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("payload must be an object")
                except ValueError:
                    payload = {}
                reply, keep_going = _handle(payload)
                conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
                if not keep_going:
                    return False


def _shutdown(_signum, _frame):
    raise KeyboardInterrupt


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m eel.server SOCKET PIDFILE", file=sys.stderr)
        return 2
    sock_path, pid_path = argv
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass
    # signals mean "stop the server" too: the compiler daemon must not outlive its parent
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    DAEMON.start()          # born together with the server
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(sock_path)
    listener.listen(8)
    try:
        with open(pid_path, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    except OSError:
        pass
    try:
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                break
            if not _serve(conn):
                break
    finally:
        DAEMON.stop()
        listener.close()
        for path in (sock_path, pid_path):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
