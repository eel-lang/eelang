"""Checks: semantics that are not in the syntax (see specs/architecture.md)."""

from __future__ import annotations

from .backend import RESERVED_NAMES
from .errors import EelError
from .ir import Module


def check(module: Module) -> None:
    """Duplicates are keyed by (name, arity): as in Erlang/Gleam,
    `fib/1` and `fib/3` are different functions. Helper names are reserved."""
    seen = set()
    for function in module.functions:
        if function.name in RESERVED_NAMES:
            raise EelError(
                f"{function.line}: name {function.name!r} is reserved by the compiler"
            )
        key = (function.name, len(function.params))
        if key in seen:
            raise EelError(
                f"{function.line}: duplicate definition of function "
                f"{function.name}/{len(function.params)}"
            )
        seen.add(key)
