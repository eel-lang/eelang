---
id: eel-patterns
type: module-design
title: Eel — patterns (pattern matching)
status: active
parent: eel-architecture
---

# Eel — patterns

A pattern is the shape of a value: it binds names and checks that the value matches. Patterns
compile to Erlang patterns of the same forms Gleam produces (pinned by the parity tests); a
mismatch **crashes the process** (let it crash) instead of returning "not found". Patterns live in
the frontend (parsing and checks) and the backend (form generation); a pattern assignment with a
constructor additionally needs a source rewrite.

## Where patterns are allowed

- **Assignment** `Pattern = expr` — valid Python: a name, a tuple/list, `*rest`.
- **Pattern assignment** `Ctor(args) = expr` — invalid Python (you cannot assign to a call), so the
  source is rewritten before parsing (see below). Allows nested constructors, lists/tuples, `*rest`,
  literals, `_`.
- **`match`/`case`** — the main syntax, plus `|` (or) and `if guard`.

## Compilation

| Pattern | Erlang |
|---|---|
| `name` | `{var, 'Name'}` — a new name; on rebinding — `X@N` |
| `_` | `{var, '_'}` |
| literal | `{integer, …}`, `{float, …}`, `{bin, …}`, `{atom, …}` |
| `(a, b)` | `{tuple, [A, B]}` |
| `[h, *t]` | `{cons, H, T}`; `[a, b]` → `{cons, A, {cons, B, nil}}` |
| `{'k': v}` | `{map, [{map_field_exact, K, V}]}` |
| `T(x, y)` | `{tuple, [{atom, t}, X, Y]}` — the tag is `snake_case`, as in Gleam |
| `p1 \| p2` | one clause per alternative, sharing the body |

`Ok`/`Error` are built in (the prelude): `Ok(x)` → `{ok, x}`, `Error(e)` → `{error, e}`.

## Rules

- **Binding**: every name becomes a new Erlang variable. Repeating a name inside one pattern is a
  compile error (otherwise Erlang would silently compare distinct variables). In `match` Python's own
  parser catches it; in an assignment pattern the frontend check does; in `x, x = …` Python simply
  rebinds the name, and eel, like Python, keeps the last value.
- **Mismatch** → crash: `badmatch` for an assignment, `case_clause` for `match`. The compiler does
  not require exhaustiveness — a difference from Gleam.
- **The value** of an assignment is the RHS: both forms produce a single `{match, Pattern, Value}`.
- **Or-patterns do not bind names** — otherwise the alternatives would diverge by variables.
- **Class patterns** check the arity against the class fields and tell a list from a tuple by the
  source brackets: `[a, b]` is a list, `(a, b)` is a tuple. Python's `match` does not distinguish
  them, so the frontend reads the brackets with the tokenizer.
- **Literals are strict** (Erlang `=:=`): `case 1` does not match `1.0`, whereas Python's `match`
  would match them. Numbers of different types are different values.
- **`*`** — one, at the end, lists only.
- Bindings made inside a `case` branch do not leak out (branch scope).

## Pattern assignment: the source rewrite

`Ctor(args) = expr` is invalid Python, so `rewrite_pattern_assignments` replaces the statement with
`__pattern_match__(Ctor(args), expr)` — a valid call — before `ast.parse`; the frontend turns it into
an `Assign` with a pattern. The line count is preserved, so error positions do not shift. In the
notebook the rewrite is installed as an input transformer (before Python's parser), so no SyntaxError
appears — at the cost of highlighting and autocomplete not understanding such a line.

## Not supported

`as` patterns; named class fields `T(x=…)`; `mod.T`; a non-literal dict key; `*` anywhere but the end,
and more than one.
