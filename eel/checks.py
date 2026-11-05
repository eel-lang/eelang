"""Проверки: семантика, которой нет в синтаксисе (см. specs/architecture.md)."""

from __future__ import annotations

from .backend import RESERVED_NAMES
from .errors import EelError
from .ir import Module


def check(module: Module) -> None:
    """Дубли определяются по паре (имя, арность): как в Erlang/Gleam,
    `fib/1` и `fib/3` — разные функции. Имена хелперов зарезервированы."""
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
