"""CLI squeal (в духе gleam): подкоманды; в первой сборке — только build."""

from __future__ import annotations

import argparse
import sys

from .compiler import build
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
    args = parser.parse_args(argv)

    if args.command == "build":
        try:
            build(args.dir, erl=args.erl, dump_forms=args.dump_forms)
            return 0
        except SquealError as e:
            print(f"squeal: {e}", file=sys.stderr)
            return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
