# Eel — build stages

A working plan, not a specification: it is not part of the spec graph and changes freely. Every
stage is closed by a concrete criterion, and the work inside a stage goes through `brainstorming`.
The scope is the first release from the goal document; NativeRecord/Map and the static analyser are
out of it. The order is test-first: the reference Gleam forms are written before the eel code.

## Stage 1 — Parity harness + end-to-end skeleton

The pytest comparison infrastructure: `gleam` 1.19.2 compiles a `.gleam` module, the forms come from
`beam_lib:chunks(Beam, [abstract_code])`, and they are compared with eel's forms after normalising
line annotations. Plus a minimal pipeline `ast.parse → IR → forms → compile:forms → .beam` driven to
the first green test.

**Criterion:** the first test pair (`def hello(): 'hi'` ≡ `pub fn hello() { "hi" }`) is green.

## Stage 2 — Values and expressions

Pairs for literals, atoms, tuples/lists/dicts, operators and calls — written before the
implementation.

**Criterion:** every pair of the stage is green; a module with all value types runs in `erl`.

## Stage 3 — Assignment and immutability

Pairs: `x = x + 1` ≡ `let x = x + 1`, `head, *rest = l` ≡ `[head, ..rest]`. The `|=` merge has no
counterpart in Gleam → a plain test. `for`/`while`/`del`/mutation bans — compile-error tests.

**Criterion:** the stage's pairs are green; `for` in code → a compile error with a position.

## Stage 4 — Branching

Pairs: `if` ≡ `case c { True -> …; False -> … }`; an exhaustive `match` ≡ an exhaustive `case`.
A non-exhaustive match ("let it crash") has no counterpart → a plain test for `{case_clause, V}`.

**Criterion:** the stage's pairs are green; an unmatched match crashes the process with `case_clause`.

## Stage 5 — Class types

Pairs: `class T(AtomPrefixed)` ≡ `pub type T { T(…) }`; class patterns ≡ constructor patterns.

**Criterion:** the stage's pairs are green; the `CustomType` example from the goal document compiles
and matches.

## Stage 6 — Lambdas and comprehensions

Pairs: `lambda` ≡ `fn`. Gleam has no comprehensions → plain tests: **20 mapping tests**, where the
right Python comprehension turns into the right Erlang one:
`[x * 2 for x in l]` → `[X * 2 || X <- L]`, `{k: v for k, v in m.items()}` → `#{K => V || K := V <- M}`,
`{k: 1 for k in m}` → `#{K => 1 || K <- eel_iter(M)}`, filters, nested generators, pattern targets of
generators, `.items()` outside a generator → `maps:to_list(M)`, a set comprehension → a compile error.

**Criterion:** the pairs and the 20 comprehension tests are green.

## Stage 7 — Modules and imports

Pairs: `from a.b.c import f` ≡ `import a/b/c` (modules `a@b@c`), output into
`build/dev/erlang/<app>/ebin`.

**Criterion:** a two-module project with a hierarchical import builds, the pairs are green.

## Stage 8 — Parity threshold

Bring the total to 10–20 parity tests (the threshold from the goal document); the missing pairs come
from the gaps of the earlier stages.

**Criterion:** pytest is green, ≥ 10–20 pairs of equivalent modules.

## Stage 9 — Errors and polish

Compiler messages with the `.eel` file and line, a README with an example, cleanup.

**Criterion:** errors point into the eel source; a newcomer can build hello-world by following the
README.

## Why this order

- **Test-first:** the oracle (Gleam's forms) exists before the eel code — every construct is checked
  against the reference from day one, so generation drift is impossible.
- **Harness first (stage 1):** the comparison and normalisation are the riskiest piece; the skeleton
  is driven to its first green test on a minimum of code.
- **2→7 by construct dependency:** values → assignment → branching (tail assignments rest on
  assignment) → classes (class patterns on top of match) → lambdas/comprehensions → modules. Every
  stage ends with a green pytest.
- **Pairs are written along the way, not at the end** — the 10–20 threshold is reached by stage 8
  instead of starting there.

## Limits of test-first (accepted)

- A pair needs a Gleam counterpart. Without pairs remain: the `|=` merge, the non-exhaustive match
  ("let it crash") and comprehensions — plain tests cover their behaviour.
- Match pairs are exhaustive only: otherwise Gleam adds a fallthrough clause that eel deliberately
  does not have.

## Alternatives considered

- **A vertical slice without the harness first** (parity as a final stage): starts faster, but the
  forms are checked against the reference late and drift accumulates.
- **Layered order** (the whole frontend → the whole IR → the whole backend): longer without a working
  result, and layer errors pile up until the end.
- **Following the goal document's examples**: the spec's examples compile from day one, but the
  whitelist surfaces in pieces later.
