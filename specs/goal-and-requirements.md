---
id: eel-goal-and-requirements
type: goal-and-requirements
title: Eel — Gleam with Python syntax
status: active
---

# Eel

A Gleam-like language for the BEAM with Python syntax; sources are `.eel` files.

## Overview

Eel is a language for the BEAM, built in the image of Gleam: the same model (immutability, custom
types instead of atoms, hierarchical modules) and the same way of compiling — emitting the Abstract
Erlang Format. The difference is the source syntax: eel is written in Python syntax and leans on
Python tooling (the stock CPython 3.15 parser; static analysis — through a fork of a Python analyser,
outside the first release).

Deliberate differences from Gleam: typing is dynamic (type annotations are for expressiveness and
documentation, checked by third-party tools, as in Python) and `match` is "let it crash" (see
Capabilities) — Gleam got carried away with types and drifted away from Erlang's principles.

## The problem

The author wants Gleam's semantics — immutability, custom types, hierarchical modules, the BEAM — but
with Python syntax and Python tooling (the stock 3.15 parser, a fork of a Python static analyser).
Gleam does not offer that: its syntax is ML-like, and on top of that it got carried away with types
and drifted away from Erlang's principles ("let it crash") — its types are not really optional.

## Capabilities

The first useful release is an eel → BEAM compiler for plain Python syntax.

- **Compilation to the BEAM.** `.eel` sources compile to the Abstract Erlang Format (the same
  low-level generation scheme Gleam uses), then to Erlang modules.
- **Syntax is plain Python.** Parsed by the stock CPython 3.15 parser. Syntax beyond Python is not
  part of this release.
- **No mutating operations.** There are no `for` loops; rebinding is allowed (`x = x + 1`). Variable
  names map to Erlang the same way as in Gleam.
- **Destructuring and merging.** `head, *rest = my_list`; `my_map |= {'key': 'value'}`.
- **Processes and messages.** `match receive():` is a selective receive with patterns and guards, the
  timeout is the `case Timeout(ms):` clause; plus `send`, `self`, `spawn(lambda: …)`.
- **Standard-library methods.** `dic.keys()`, `s.split(",")`, `xs.count(1)` — Python's method-call
  syntax on an stdlib value (`str`, `dict`, `list`, `tuple`). These are calls to OTP functions, not
  objects: the method list is closed (a compiler whitelist), third-party code cannot define methods,
  and there are no mutating methods.
- **Redefinable builtins.** `print`, `zip`, `enumerate` are ordinary names: the user may define a
  function with the same name (by name, as in Python), and the builtin is then not used. Prelude
  types are redefinable the same way: your own `class Ok(AtomPrefixed)` replaces `Ok`.
- **range.** `range(stop)`, `range(start, stop)`, `range(start, stop, step)` — Python semantics
  (the stop is excluded), eager: it returns a list; integers only.
- **zip and enumerate.** `zip(a, b)` yields pairs up to the shorter list; `enumerate(xs[, start])`
  yields `(index, value)` pairs. Eager (they return a list) and list-only.
- **Nested functions.** `def` inside a body is a closure value (`fun`): it behaves like a nested
  Python function, with recursion and decorators, and Python name semantics (a plain variable).
- **print.** `print(x)` is built-in output to stdout with Python-like value display (a string as
  text, an atom without quotes, a list/tuple/dict in Python form); exactly one argument, returns
  `nil`.
- **Hierarchical imports.** Python syntax (`from my.custom.pak import my_fun`), the module system is
  like Gleam's.
- **if and case in Python syntax.** The compiler checks that the last operation of every branch is an
  assignment to the same variable.
- **match..case with "let it crash".** Exhaustiveness is not required: if no case matched, it is a
  runtime error and the process dies. Gleam would refuse to compile such code.
- **Atoms and custom types.** Atoms use single quotes (`'ok'`); types are recommended instead of
  atoms, as in Gleam. `class CustomType(AtomPrefixed)` with fields `x: int, y: int` (annotations are
  documentation only) compiles to the tuple `{custom_type, x, y}`. Also: `class CustomRecord(NativeRecord)`
  — the native OTP 29 record that keeps its type at runtime; `class CustomMap(Map)`.

## Technical notes

- **Implemented in Python.** The parser is stock CPython 3.15 through the Python package, not a fork —
  unlike eel_beam.
- **Gleam's code is not reused**, but the abstractions are the same; especially the low level: the
  generation of the Abstract Erlang Format, where identical syntax nodes must produce identical code.
- **Parity tests.** 10–20 tests checking that Gleam and eel produce the same code for equivalent
  sources.
- **Static analysis** — a fork of a Python static analyser (an option being PyCharm's Kotlin
  analyser); not part of the first release. Type checking is left to third-party tools, as in Python.
- **Reference material** — `~/rw/eel_beam/eel`: the only good thing about eel_beam is that it is
  working code; looking over its shoulder is allowed.
