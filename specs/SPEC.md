---
id: eel-compiler-package
type: module-design
title: Eel — the compiler package
status: active
parent: eel-architecture
---

# Eel — the compiler package

The `eel/` package turns an `.eel` source into the Erlang Abstract Format and a `.beam`. The language
rules live in architecture.md, the pattern rules in `eel-patterns` (patterns.md); here we cover the
package's responsibilities, its boundaries and its stages.

## Responsibilities

- `python -m eel build DIR` compiles the `src/*.eel` tree into `build/dev/erlang/<app>/ebin` (`<app>`
  is the project directory name); the forms go into `build/dev/erlang/<app>/_eel_artefacts/forms.etf`
  as an ETF blob (`.abstr`/`.erl` only with the `--dump-forms`/`--erl` flags).
- Compile errors are `eel: <path>:<line>: <text>` on stderr, exit 1 (no traceback). The message text
  is English (lowercase, no trailing period); the docs, comments and docstrings are English too.
- The package shells out only to `escript` (the compilation driver). The parser is the stock
  `ast.parse` from CPython 3.15; `compile()` is not called, so Python's checks do not get in the way of
  our semantics.

## Stages

- `frontend.py` — Python AST → IR. Atoms and strings are told apart by the source quotes (`'ok'` is an
  atom, `"ok"` a string): `ast` does not keep the quoting style, so the frontend recovers it with the
  tokenizer. Function bodies are sequences of statements: assignment (name/list/tuple), `return`, an
  expression; `|=` — a map merge; `if`/`else` and `match`/`case` (patterns: literals, `_`, capture, or,
  lists/tuples, dict; guard); `for`/`while`/`del`/AugAssign (other than `|=`)/assignment through an
  index-attribute are errors. Classes `AtomPrefixed`: the constructor `T(…)` is inlined into the tuple
  `{t, …}` and the pattern `case T(…)` → `{t, …}`; `snake_case` is a port of Gleam's; `NativeRecord`/`Map`
  are errors; constructor and class-pattern arity is checked; decorators are allowed on functions only
  (`@dec` resolving through imports/scope, `@mod.dec` → `fun mod:dec/1`, and calls; arity 1 is checked
  statically); the built-ins `Ok`/`Error` (the prelude) → `{ok, …}`/`{error, …}`; a user `class Ok`/
  `class Error` overrides the prelude (a second user definition is an error).
  `lambda` → IR; list/dict comprehensions (every generator through `eel_iter`,
  `for k, v in m.items()` → a map generator, the `.items()` receiver may be any expression); a method
  call → `ir.Method` (name, arity, receiver); the built-ins `print`/`zip`/`enumerate`/`range` are
  ordinary `ir.Call`s (resolved in the backend as a fallback, which is why they are redefinable by
  name); a `def` inside a function body → `ir.NestedDef`; a set comprehension or a generator expression
  is an error. `from a.b.c import f, g as h` — imports (several names, the `as` alias); `import *` and
  `import X` are errors.
- `ir.py` — the IR nodes; separates the backend from the Python AST so the frontend can be replaced by
  a parser of our own.
- `checks.py` — semantics that are not in the syntax (duplicates by (name, arity), reservation of
  helper names including `eel_on_load`).
- `backend.py` — IR → Erlang Abstract Format; the forms repeat Gleam's, the annotation is the `.eel`
  line; the Python-semantics operators (`// % **`) are the helpers `eel_floordiv`/`eel_mod`/`_eel_pow`
  (int and float), generated on demand; helper names are reserved; a linear scope per function maps
  names as Gleam does (`x` → `X`, rebinding → `X@N`) and destructuring becomes a `match` with a
  pattern; `if`/`match` → a `case` expression with the target assignment lifted out (the branches must
  agree on the target); in a tail position of the body the `case` is the value itself, without a
  `match` wrapper; `lambda` → `{'fun', …}`, a call of a name from scope → `{call, {var, …}}`;
  definitions and calls are told apart by (name, arity); comprehensions → `lc`/`mc`/`m_generate`;
  a nested `def` → `named_fun` (recursion) with the name bound as an ordinary variable and decorators
  applied at once (`F = Dec(fun …)`), a name in `_scope` resolves as a variable, also in decorators;
  `eel_iter/1` is generated on demand (map → keys, list → as is); decorators —
  `f@impl/N` plus a wrapper through `persistent_term` and `eel_on_load/0` (`-on_load`);
  stdlib methods are a table in the backend: `str` → `string:*`/`binary:*` under an `is_binary` check,
  `dict` → `maps:*`, `list`/`tuple` → `count` through a `case` over `is_list`/`is_tuple` (fresh binder
  names `_eel@…`), a wrong receiver → `erlang:error({bad_receiver, Method, Value})`, an unknown
  name/arity → `EelError`;
  a call of a name found nowhere but present in `BUILTINS` → the builtin form (`print` — exactly 1
  argument, `zip` — 2, `enumerate` — 1 or 2, `range` — 1–3, `send` — 2, `self` — 0, `spawn` — 1,
  otherwise `EelError`);
  `zip`/`enumerate` → the helpers `eel_zip/2`, `eel_enumerate/2` (lists only, a non-list →
  `{bad_argument, Name, V}`), `range` → `eel_range/3` (start, stop, step; integers, Python semantics) —
  all generated on demand;
  `match receive():` (subject — `ir.Receive`) → `{'receive', Clauses[, Timeout, AfterBody]}`: a
  selective receive, the `case Timeout(ms):` clause becomes `after`; `send`/`self`/`spawn` →
  `erlang:'!'/2`, `erlang:self/0`, `erlang:spawn/1`;
  `print` → `eel_print/1` (`io:format("~ts~n")`), with the helpers `eel_str/1`, `eel_str_tuple/1`,
  `eel_str_map/1` — the Python-like value display, generated on demand;
  `term.py` prints Erlang floats (`1e-5` → `1.0e-5`) and strings (`\x{HEX}` for non-ASCII/control
  characters); an import → `{call, {remote, a@b@c, f}}` (a name from scope wins over an import).
- `term.py` — `Atom` and term printing to text for `file:consult`.
- `eel_compile.escript` — the server: `{EtfPath, ErlFlag}.` requests from stdin, `OK` / `ERR <base64>`
  answers; it decodes the ETF, calls `compile:forms` (+ `return`), writes `.beam` (and `.erl` with the
  flag, through `erl_pp`); lint errors are formatted as `path:line: text` (no dump ever reaches the
  outside).
- `client.py` — the thin client of the build server (socket/json/pathlib, no compiler import): the
  socket/pid/log paths, `ping`/`stop`/`build_remote`, server autostart.
- `server.py` — the build server: `python -m eel.server <sock> <pid>`, JSON lines
  `{"op": build|ping|stop}`, a sequential accept loop, a call to `build()`; it starts the compiler
  daemon at startup and stops it in `finally` (including `SIGTERM`/`SIGINT`), so the daemon lives
  exactly as long as the server.
- `etf.py` — an External Term Format encoder: forms → a blob, no dependencies. `str` is encoded as a
  **charlist** (in the forms it is `{string, L, "chars"}`), `int` — including arbitrary precision,
  `float`, `tuple`, `list`; zlib compression when it pays off.
- `compiler.py` — the pipeline (`compile_source`, `build` with the incremental `build.json` manifest
  and `--force`), `CompilerDaemon` (a background escript on a unix socket: autostart, `ping`/`stop`,
  protocol version check, fallback when unreachable) and `CompilerProcess` (an escript inside the
  process — for `--no-daemon` and as the fallback path; it never starts the daemon itself, which lives
  only as a child of the build server, and in `--daemon` mode the escript watches its parent through
  stdin EOF);
  `__main__.py` — the command line (`build [DIR] [--erl] [--dump-forms] [--no-daemon] [--force]`,
  `daemon start|stop|status`); a build goes through the thin client of the server, and only falls back
  to the in-process path.
- `session.py` — shell mode: a cell → forms; the session keeps the name mapping/classes/imports.
- `repl.py` + `repl.escript` — the persistent Erlang REPL (`erl_eval:exprs`, bindings live; `'$pp'` —
  `erl_pp` of a form, for printing the equivalent; `print` output accumulates in the process dictionary
  and is sent as an `OUT <base64>` frame before the `OK …` line, and the client writes it to stdout).
- `notebook.py` — the ipykernel hook (`pre/post_run_cell` + an `ast_transformer` replacing the body
  with `pass` + an input transformer for pattern assignments); before the result of a comprehension it
  prints `erl: …`; a `nil` cell value is not printed.

## Boundaries and limits

- Stage 7: the top level allows `def`, `class`, `from … import …`; the tree is `src/**` (module
  `a@b@c`) and the output goes into `build/dev/erlang/<app>/ebin`. Out of scope: `import X`/`import *`,
  generator expressions, `NativeRecord`/`Map`, `is`/`is not`, comparison chains, loops and mutation
  (forbidden).
- There are no types and no `-spec` is generated (see architecture.md).
