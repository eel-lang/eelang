"""Eel's intermediate representation.

Corresponds to Gleam's ``TypedExpr``/``TypedModule`` layer, but without types
(see specs/architecture.md). The nodes carry the ``.eel`` line the backend puts into
the annotation of the Erlang form.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# --- expressions ---


@dataclass(frozen=True)
class Int:
    value: int
    line: int


@dataclass(frozen=True)
class Float:
    value: float
    line: int


@dataclass(frozen=True)
class String:
    value: str
    line: int


@dataclass(frozen=True)
class AtomLit:
    name: str
    line: int


@dataclass(frozen=True)
class Bool:
    value: bool
    line: int


@dataclass(frozen=True)
class Nil:
    line: int


@dataclass(frozen=True)
class Var:
    name: str
    line: int


@dataclass(frozen=True)
class Tuple:
    items: tuple
    line: int


@dataclass(frozen=True)
class List:
    items: tuple
    line: int


@dataclass(frozen=True)
class Dict:
    items: tuple  # ((key, value), ...)
    line: int


@dataclass(frozen=True)
class UnaryOp:
    op: str  # USub | UAdd | Not
    operand: object
    line: int


@dataclass(frozen=True)
class BinOp:
    op: str  # Add|Sub|Mult|Div|FloorDiv|Mod|Pow|Eq|NotEq|Lt|LtE|Gt|GtE
    left: object
    right: object
    line: int


@dataclass(frozen=True)
class BoolOp:
    op: str  # And | Or
    operands: tuple
    line: int


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple
    line: int


@dataclass(frozen=True)
class FunRef:
    """A bare name in a decorator position → `fun name/1`."""

    name: str
    line: int


@dataclass(frozen=True)
class RemoteFunRef:
    """`mod.name` in a decorator position → `fun mod:name/1`."""

    module: str
    name: str
    line: int


@dataclass(frozen=True)
class RemoteCall:
    """`mod.name(args)` — a direct call of an Erlang module (decorators)."""

    module: str
    name: str
    args: tuple
    line: int


@dataclass(frozen=True)
class Merge:
    """``m |= d`` — a map merge."""

    left: object
    right: object
    line: int


# --- branch patterns ---


@dataclass(frozen=True)
class Wildcard:
    line: int


@dataclass(frozen=True)
class OrPattern:
    patterns: tuple
    line: int


@dataclass(frozen=True)
class PMap:
    items: tuple  # ((key_expr, subpattern), ...)
    line: int


@dataclass(frozen=True)
class If:
    condition: object
    then_body: tuple  # statements
    else_body: tuple
    line: int


@dataclass(frozen=True)
class Case:
    pattern: object
    guard: object  # an expression or None
    body: tuple
    line: int


@dataclass(frozen=True)
class Receive:
    """``receive()`` — the next message from the mailbox (blocking)."""

    line: int


@dataclass(frozen=True)
class Match:
    subject: object
    cases: tuple  # (Case, ...)
    line: int
    # `case Timeout(ms): …` inside `match receive():` — Erlang's `after ms -> …` branch
    after_timeout: object | None = None
    after_body: tuple = ()


# --- lambdas and comprehensions ---


@dataclass(frozen=True)
class Lambda:
    params: tuple
    body: object
    line: int


@dataclass(frozen=True)
class Def:
    """`def f(x): …` in shell mode → the fun value `F`."""

    name: str
    params: tuple
    statements: tuple  # the body (statements)
    line: int
    decorators: tuple = ()


@dataclass(frozen=True)
class NestedDef:
    """`def f(x): …` inside a body — a nested function: a fun bound to a name."""

    name: str
    params: tuple
    statements: tuple
    line: int
    decorators: tuple = ()


@dataclass(frozen=True)
class Method:
    """A standard-library method call: `recv.name(args)`."""

    receiver: object
    name: str
    args: tuple
    line: int


@dataclass(frozen=True)
class Gen:
    target: object  # a pattern
    iter: object
    line: int


@dataclass(frozen=True)
class MapGen:
    """``for k, v in m.items()`` — the map generator ``K := V <- M``."""

    key: object
    value: object
    map: object
    line: int


@dataclass(frozen=True)
class Filter:
    expr: object
    line: int


@dataclass(frozen=True)
class ListComp:
    element: object
    qualifiers: tuple  # (Gen | MapGen | Filter, ...)
    line: int


@dataclass(frozen=True)
class DictComp:
    key: object
    value: object
    qualifiers: tuple
    line: int


# --- assignment patterns ---


@dataclass(frozen=True)
class PName:
    name: str
    line: int


@dataclass(frozen=True)
class PStar:
    name: str
    line: int


@dataclass(frozen=True)
class PList:
    items: tuple  # PName | PStar (the star is last only)
    line: int


@dataclass(frozen=True)
class PTuple:
    items: tuple  # PName
    line: int


# --- statements ---


@dataclass(frozen=True)
class Assign:
    target: object  # PName | PList | PTuple
    value: object
    line: int


@dataclass(frozen=True)
class Expr:
    """An expression statement: the last one's value is the function's result."""

    value: object
    line: int


@dataclass(frozen=True)
class Function:
    name: str
    params: tuple
    statements: tuple  # (Assign | Expr, ...)
    line: int
    decorators: tuple = ()


@dataclass(frozen=True)
class Module:
    name: str
    path: str
    functions: tuple
    imports: dict = field(default_factory=dict)
