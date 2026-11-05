"""CLI squeal (в духе gleam): подкоманды; в первой сборке — только build."""

from __future__ import annotations

import argparse
import sys

from .compiler import DAEMON, build, daemon_log, daemon_pid_file, daemon_socket
from .errors import SquealError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m squeal")
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
        help="compile in-process instead of using the background daemon",
    )
    d = sub.add_parser("daemon", help="background compiler daemon")
    d.add_argument("action", choices=["start", "stop", "status"])
    args = parser.parse_args(argv)

    if args.command == "build":
        try:
            build(
                args.dir,
                erl=args.erl,
                dump_forms=args.dump_forms,
                daemon=not args.no_daemon,
            )
            return 0
        except SquealError as e:
            print(f"squeal: {e}", file=sys.stderr)
            return 1
    if args.command == "daemon":
        return _daemon(args.action)
    return 1


def _daemon(action: str) -> int:
    if action == "start":
        info = DAEMON.ensure()
        print(f"compiler daemon: running (pid {_pid()}, socket {daemon_socket()})")
        print(f"  protocol {info.get('version')}, compilations so far {info.get('compiled')}")
        return 0
    if action == "stop":
        stopped = DAEMON.stop()
        print("compiler daemon: stopped" if stopped else "compiler daemon: not running")
        return 0
    info = DAEMON.ping()
    if info is None:
        print("compiler daemon: not running")
        return 1
    print(f"compiler daemon: running (pid {_pid()}, socket {daemon_socket()})")
    print(f"  protocol {info.get('version')}, compilations so far {info.get('compiled')}")
    print(f"  log {daemon_log()}")
    return 0


def _pid() -> str:
    try:
        return daemon_pid_file().read_text().strip()
    except OSError:
        return "?"


if __name__ == "__main__":
    sys.exit(main())
