# Methods (OOP) — proposal

A working document (not a spec): the method list as approved and amended.

## Principles

- Methods exist **only** on standard-library structures; third-party code cannot define methods.
- **Non-mutating only**: Python's mutating methods (`append`, `sort`, `update`, …) change the object
  and return `None` — eel has no mutation.
- **Dispatch by the value's type** at runtime: the receiver is an ordinary value, its type is checked
  at the call (`is_binary` / `is_map` / `is_list` / `is_tuple`); a wrong type crashes (`badarg`/`badmap`).
- The method list is a table in the compiler; an unknown method is a compile error (as before).

## dict (map)

| Method | Python | Erlang |
|---|---|---|
| `keys()` | list of keys | `maps:keys/1` |
| `values()` | list of values | `maps:values/1` |
| `items()` | list of `(k, v)` pairs | `maps:to_list/1` |
| `get(k)` | the value, or `None` | `maps:get(K, M, nil)` |
| `get(k, d)` | the value, or `d` | `maps:get(K, M, D)` |

## str (binary)

| Method | Erlang |
|---|---|
| `startswith(p)` | `string:prefix(S, P) =/= nomatch` |
| `endswith(p)` | `binary:part/3` — comparing the tail (OTP has no `string:suffix/2`) |
| `lower()` | `string:lowercase/1` |
| `upper()` | `string:uppercase/1` |
| `strip()` | `string:trim/1` (both ends, whitespace) |
| `lstrip()` | `string:trim(S, leading)` |
| `rstrip()` | `string:trim(S, trailing)` |
| `split(sep)` | `string:split(S, Sep, all)` |
| `split()` | `string:lexemes(S, " \t\n\r\f\v")` — runs of whitespace collapse |
| `join(parts)` | the `eel_join/2` helper: parts must be strings, otherwise `{bad_argument, join, Part}` |
| `replace(old, new)` | `binary:replace(S, Old, New, [global])` |

## list / tuple

| Method | Erlang |
|---|---|
| `count(x)` | the number of elements `=:=` X |

## set

**Skipped**: eel has no sets — no literal, no comprehension, no value. Methods on `set` would first
need a separate feature with a set representation (say, on `ordsets`/`maps`).

## Explicitly out (mutating)

`append`, `extend`, `insert`, `remove`, `pop`, `clear`, `sort`, `reverse`, `update`, `setdefault`,
`popitem`, `add`, `discard`, `difference_update`, …

## Name collisions

Only one name really collides; it dispatches with a `case` over the receiver's type:

- `count/1` — `list` (an element), `tuple` (an element). No `count` for str.

Every other name is unique to its type.

## Questions to the author

1. Does `find` return `-1` (like Python) or `nil`? — not needed
2. `list.index` — crash on a miss (Python: `ValueError`) or return `-1`? — not needed
3. Do we want `xs.sorted()` / `xs.reversed()` (Python has no such methods — it has `sorted()`/`reversed()`)? — no
4. Do we want `isdigit()`, `isalpha()`, `isspace()`, `capitalize()`, `title()`, `splitlines()`? — only `splitlines`
5. Do we want arguments beyond the basic ones: `strip(chars)`, `split(sep, maxsplit)`, `replace(old, new, count)`? — no
6. `startswith`/`endswith` — may they take a tuple of alternatives, as in Python? — no
