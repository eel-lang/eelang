"""The eel CLI (in the spirit of gleam): subcommands; the first release knows only build."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eel")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="compile the src/ tree into build/dev/erlang")
    b.add_argument("dir", nargs="?", default=".", help="project root (default: .)")
    b.add_argument(
        "--erl",
        action="store_true",
        help="also write .erl source next to each .beam",
    )
    b.add_argument(
        "--dump-forms",
        action="store_true",
        help="also write readable .abstr forms (debugging)",
    )
    b.add_argument(
        "--no-daemon",
        action="store_true",
        help="compile in this process (no background server)",
    )
    b.add_argument(
        "--force",
        action="store_true",
        help="rebuild every module, ignoring the incremental manifest",
    )

    d = sub.add_parser("daemon", help="background build server and compiler daemon")
    d.add_argument("action", choices=["start", "stop", "status"])

    args = parser.parse_args(argv)
    if args.command == "build":
        return _build(args)
    if args.command == "daemon":
        return _daemon(args.action)
    return 1


def _build(args) -> int:
    """The thin server client first; building in-process is the fallback."""
    from . import client
    from .errors import EelError

    if not args.no_daemon:
        try:
            if client.build_remote(
                args.dir, erl=args.erl, dump_forms=args.dump_forms, force=args.force
            ):
                return 0
        except EelError as exc:
            print(f"eel: {exc}", file=sys.stderr)
            return 1

    from .compiler import build

    try:
        build(
            args.dir,
            erl=args.erl,
            dump_forms=args.dump_forms,
            daemon=not args.no_daemon,
            force=args.force,
        )
        return 0
    except EelError as exc:
        print(f"eel: {exc}", file=sys.stderr)
        return 1


def _daemon(action: str) -> int:
    from . import client
    from .compiler import DAEMON, daemon_log, daemon_pid_file

    if action == "start":
        info = client.ensure()
        print(f"build server: running (pid {_pid(client.server_pid_file())}, socket {client.server_socket()})")
        print(f"  protocol {info.get('version')}, builds so far {info.get('builds')}")
        return 0

    if action == "stop":
        stopped_server = client.stop()
        stopped_compiler = DAEMON.stop()
        print(
            "build server: stopped" if stopped_server else "build server: not running"
        )
        print(
            "compiler daemon: stopped" if stopped_compiler else "compiler daemon: not running"
        )
        return 0

    info = client.ping()
    if info is None:
        print("build server: not running")
    else:
        print(
            f"build server: running (pid {_pid(client.server_pid_file())}, "
            f"socket {client.server_socket()})"
        )
        print(
            f"  protocol {info.get('version')}, builds {info.get('builds')}, "
            f"errors {info.get('errors')}"
        )
        print(f"  log {client.server_log()}")

    compiler_info = DAEMON.ping()
    if compiler_info is None:
        print("compiler daemon: not running")
    else:
        print(
            f"compiler daemon: running (pid {_pid(daemon_pid_file())}, "
            f"socket {daemon_socket_path()})"
        )
        print(
            f"  protocol {compiler_info.get('version')}, "
            f"compilations so far {compiler_info.get('compiled')}"
        )
        print(f"  log {daemon_log()}")
    return 0 if info is not None else 1


def daemon_socket_path():
    from .compiler import daemon_socket

    return daemon_socket()


def _pid(path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return "?"


if __name__ == "__main__":
    sys.exit(main())
