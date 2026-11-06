---
id: eel-architecture
type: architecture-design
title: "Eel — the compiler: architecture"
status: active
parent: eel-goal-and-requirements
---

# Eel — the compiler: architecture

Two parts: the language rules — how eel maps onto Erlang — and the shape of the compiler (the
`.eel` → `.beam` pipeline).

## Language rules

### Names and modules

- A module is the path of its file under `src/`: `src/a/b/c.eel` → the Erlang module `a@b@c` (the
  `@` separator, as in Gleam).
- `from a.b.c import f` → the call `a@b@c:f(...)`; `from a.b.c import f, g as h` — several names and
  an alias (`h` → `a@b@c:g`); `import *` and `import X` are errors.
- Variables map as in Gleam: the first binding of a name is `X`, every further rebinding is `X@1`,
  `X@2`, … (`x = x + 1` → `X@1 = X + 1`).

### Values

- A string `"..."` (double quotes) → the binary `<<"..."/utf8>>`; an atom `'ok'` (single quotes) →
  an atom — the quotes tell a string from an atom.
- `True`/`False` → `true`/`false`; `None` → `nil`; `int`/`float` → an Erlang number.
- A tuple `(a, b)` → `{a, b}`; a list `[a, b]` → `[a, b]`; a dict `{'k': v}` → `#{k => v}` (atom key),
  `{"k": v}` → `#{"k" => v}` (binary key).

### Operators

- `+ - * < <= > >=` — as in Gleam (the same forms); `!=` → `/=`.
- `==` → Erlang `==` (coercing, `1 == 1.0` is true — Python semantics); `is`/`is not` are compile errors.
- `and`/`or`/`not` → `andalso`/`orelse`/`not`, operands must be bool (anything else — `badarg`).
- `// % **` — Python semantics through helpers (int and float): `%` → `eel_mod` (`rem` corrected for
  the divisor's sign; for floats — `math:fmod`), `//` → `eel_floordiv` (`div` corrected; for floats —
  `math:floor(A / B)`), `**` → `_eel_pow` (integer for int**int(≥0), otherwise `math:pow`). Division
  by zero crashes (`badarith`). Helper names are reserved.
- `/` — raw Erlang `/` (float division; by zero — `badarith`).

### Class types

- `class T(AtomPrefixed): x: int` — annotations are documentation only; the class is never emitted
  into the runtime. The constructor `T(…)` is **inlined** into the tuple `{t, …}` (the tag is the
  class name in snake_case, as in Gleam; there is no constructor function); the number of arguments
  of a constructor and of the pattern `case T(x):` is checked against the number of fields.
- Built in (the prelude, as in Gleam): `Ok(x)` → `{ok, x}`, `Error(e)` → `{error, e}`; the patterns
  `case Ok(v):`/`case Error(e):` are the same tuples. Both names are **redefinable**: `class Ok(AtomPrefixed)`
  (and `Error` likewise) replaces the prelude — the user's version with its own field count is used
  (the tag stays `ok`/`error`, the form `{ok, …}`); defining a user class with the same name twice is
  still an error.
- `class R(NativeRecord)` and `class M(Map)` — the native OTP 29 record and map; the exact mapping is
  unconfirmed (a compile error for now).

### Immutability and assignment

- `for`, `while`, `del`, `+= -= *= /=` and assignment through an index/attribute are compile errors
  (see Checks).
- Rebinding a name is allowed: a new Erlang name by the `X@N` scheme (the parameter is the first
  binding `X`). An undefined name is a compile error.
- Destructuring (`head, *rest = …`, `a, b = t`, the pattern assignment `Ctor(…) = …`) — patterns, see
  `eel-patterns`.
- `my_map |= {'k': v}` → `maps:merge(M, D)`; a non-map — `badmap` (let it crash).

### Branching

- `if c: a else: b` → `case C of true -> A; false -> B end`: only `true` is truthy; any other value
  crashes the process (`case_clause`) — "let it crash".
- `match v: case p1: … case p2: …` → `case V of … end` with no fallthrough: when no pattern matched,
  the runtime crashes with `{case_clause, V}`.
- Patterns (`|`, guards, capture, `_`, lists/tuples/dicts, constructors) and their compilation live in
  `eel-patterns` (patterns.md).
- Variables bound inside a branch do not leak out (branch scope); `if` without `else` is a compile error.
- In a normal position every branch of `if`/`match` ends with an assignment to the same variable (a
  compiler check) — the branches become a `case` and its value is assigned to that variable.
- In a **tail position** (the last statement of a function body or of a notebook cell) the tail
  assignment is not required: branches may be any expressions and the function returns the `case`
  value. If all branches still assign the same variable, it is bound outside as before.
- `_` is the wildcard; patterns are literals, classes, lists/tuples/dicts — as in Python.

### Functions, lambdas, comprehensions

- `def f(a, b): …` → `f(A, B) -> …`; a function's value is its last statement (an assignment counts
  as a value). Every function of the module is exported.
- **Decorators** — on functions only, Python syntax: `@dec`, `@mod.dec`, `@dec(args)`, `@mod.dec(args)`;
  applied **once** (as in Python), a chain from the inside out. In a module the body moves into a
  private `f@impl/N`, `f/N` wraps the value from `persistent_term`, and `eel_on_load/0` (the `-on_load`
  attribute) applies the decorators at load time (a reload recomputes). In the notebook a placeholder
  goes through the process dictionary; calls inside the body go through the decorator. A decorator is
  a function of arity 1; its name resolves like this: a variable/nested function from scope → a call
  through the variable, an imported name → `fun mod:dec/1`, otherwise a module function (which is
  checked to exist with arity 1; otherwise a compile error with the `@` line). `@mod.dec` is
  `fun mod:dec/1` with no import requirement: a call of a module by name does not need an import
  anyway.
- **A nested `def`** is an ordinary Python nested function: it compiles to a `fun` bound to a name as
  a value (like `lambda`, but with a name and a body of statements). Recursion works (`named_fun`);
  mutual recursion between two nested functions does not (a call before the definition is a compile
  error). The name is an ordinary variable: a second `def f` in the same body shadows the first
  (`F` → `F@1`) and the arity does not distinguish them; a nested name also shadows a module/cell
  function. Capture is by value at the definition point (as with `lambda`). Decorators on a nested
  `def` are applied immediately at the definition (`F = Dec(fun …)`, inside out); a recursive call
  inside a decorated nested function goes into its body, bypassing the decorator (for module
  functions it goes through the decorator).
- **Arity is part of a function's identity** (as in Gleam): `def fib(x)` and `def fib(x, c, n)` are the
  different functions `fib/1` and `fib/3`. In a module they are two Erlang functions, defined in any
  order; a duplicate (name, arity) pair is an error. In the notebook a call resolves only to an
  already defined arity (a fun does not allow forward references) — the definition must come before
  the call, otherwise a compile error with a hint.
- `lambda x: e` → `fun(X) -> E end`; the closure captures a value (as in Gleam), not Python's late
  binding: rebinding the name after the lambda does not change the value already captured.
- `[e for x in l if c]` → `[E || X <- L, C]`; `{k: v for …}` → a map comprehension (OTP 26+).
  Generators, filters and nesting are transferred 1:1 in the same order; a generator's pattern target
  skips non-matching elements — both in Python and in Erlang. A filter keeps on `true` and skips
  otherwise (Erlang semantics; unlike `if`, which crashes). Eel has no sets → a set comprehension is a
  compile error.
- Dict iteration: `for k in m` → `K <- eel_iter(M)` — a map yields keys, a list yields elements,
  anything else is a `bad_generator` (let it crash). Every list/dict comprehension generator goes
  through `eel_iter` (one semantics: dict → keys, list → elements). Pairs are explicit:
  `for k, v in m.items()` → the map generator `K := V <- M` (no `maps:to_list`; a non-map — `bad_generator`).
- **Methods** — only on standard-library structures, non-mutating only; third-party code cannot define
  methods (the language has no objects or classes with methods). The list is a whitelist in the
  compiler: an unknown name or arity is a compile error. The receiver's type is **not** checked by the
  compiler (dynamic): a method call is a call to an OTP function, and a type mismatch crashes the
  process (let it crash). For methods that do check the type the reason is named:
  `{bad_receiver, <method>, <value>}` (`'ok'.startswith(…)` → `{bad_receiver,startswith,ok}`); dict
  methods surface `maps:*` errors (`{badmap, V}`). The values and their methods:
  - `str` (a binary): `startswith(p)`, `endswith(p)`, `lower()`, `upper()`, `strip()`, `lstrip()`,
    `rstrip()`, `split(sep)`, `split()`, `join(parts)`, `replace(old, new)`, `splitlines()` —
    `string:*`/`binary:*`; the receiver is checked with `is_binary` (otherwise `badarg`), because
    `string:*` would also accept a chardata list; a non-string (say the atom `'ab'`) →
    `{bad_receiver,…}`. String arguments are strings (`"ab"`), not atoms.
    `split()` (no argument) collapses runs of whitespace; `splitlines()` knows `\r\n`, `\n`, `\r`
    (the other Unicode separators of `str.splitlines` are outside the first release) and produces no
    empty tail: `"a\n".splitlines()` → `["a"]`, `"".splitlines()` → `[]` — as in Python.
  - `dict` (a map): `keys()`, `values()`, `items()`, `get(k)` (a missing key gives `nil`), `get(k, d)` —
    `maps:*`.
  - `list`/`tuple`: `count(x)` — by `=:=` (strict, as patterns are: `1` is not `1.0`).
  - `join` checks its parts: strings only, otherwise `{bad_argument, join, Part}` — `iolist_to_binary`
    would otherwise silently turn integers into bytes.
  - Python's mutating methods (`append`, `sort`, `reverse`, `update`, `pop`, …) do not exist: there is
    no mutation.
  - `set` — impossible: eel has no set values (a set comprehension is an error).
  `items()` remains a special case inside comprehensions: `for k, v in m.items()` → the map generator
  `K := V <- M` (a non-map — `bad_generator`), the expression `m.items()` → `maps:to_list(M)` (a
  non-map — `badmap`).

### Processes and messages

- `receive()` is the next message from the mailbox; it blocks (`receive X -> X end`).
- `match receive(): case p: … ` is a **selective** receive: the clauses compile into an Erlang
  `receive` as a whole, so the runtime itself scans the mailbox, guards work, and a message that did
  not match stays in the mailbox. When no pattern matches we wait (as Erlang does, with no "let it
  crash"). The branch rules are the same as for `match` (the same target variable in a normal
  position, the `receive` value in a tail position).
- `case Timeout(ms): … ` is the timeout branch, compiled into `after ms -> …`; it must be the last one,
  a guard on it is forbidden, and it may be the only clause (`receive after T -> …`). `Timeout(…)` is a
  marker only inside `match receive():`; elsewhere it is an ordinary class pattern. A limitation of
  Python's grammar: an irrefutable pattern (`_` or a name capture) must be the last `case`, so it
  cannot share a branch with `Timeout` — before `Timeout` you need a refutable pattern
  (`case Ok(v):`) or only `Timeout`.
- `send(pid, msg)` → `Pid ! Msg`; `self()` → your own pid; `spawn(lambda: …)` → a new process
  (`erlang:spawn/1` on a fun), returning a pid. The names `send`/`self`/`spawn`/`receive` are built in
  and, like the others, redefinable with your own `def` (name resolution happens in the backend, see
  below).
- Out of scope: `spawn_link`, `monitor`, links, OTP behaviours, `after` outside `match receive():`.

### Built-in functions

- `print(x)` — output to stdout, exactly one positional argument; `print()`, `print(a, b)` and
  `print(x, sep=…)` are compile errors. It returns `nil` (like `None` in Python) — usable in an
  expression.
- **Built-in names are redefinable** (as in Python, by name): `def print(x)`, `def zip(a, b)`,
  `def enumerate(xs)` give ordinary functions, and in that scope the builtin is no longer used — even
  if the user's function has a different arity (then `print(1)` is the error "function print/1 is not
  defined (have: print/2)"). A builtin is the **fallback** of name resolution: the backend looks in
  the scope, the definitions and the imports first. Only helper names are reserved (`eel_print/1`,
  `eel_zip/2`, …). A builtin is not a value (`f = print` is not supported).
- The display is Python-like: a string as text (`print("hi")` → `hi`), an atom as a name without
  quotes (`ok`, `true`, `nil`; Python's `True`/`False`/`None` are not substituted), a number as a
  number, a list as `[1, a]`, a tuple as `(1, 2)` (single-element `(1,)`, empty `()`), a dict as
  `{a: 1}` with keys in sorted order (the BEAM has no order), a fun/pid/ref as its Erlang
  representation. Strings are unquoted everywhere, including inside containers: `print(["hi"])` →
  `[hi]`. Floats use `float_to_binary(F, [short])` (`1.0` → `1.0`); rare forms like `1.0e-5` versus
  Python's `1e-05` are a known difference.
- `zip(a, b)` and `enumerate(xs[, start])` are eager and return a list: `zip` → `[{a, b}, …]` up to the
  end of the shorter list (`lists:zip/3` with `trim`, as in Python; exactly two arguments),
  `enumerate` → `[{N, x}, …]` with `N` from `start` (0 by default; `lists:enumerate/2`). They accept
  **lists only**: anything else (a dict included) is `{bad_argument, zip|enumerate, Value}`; `for` on
  the other hand iterates a dict by keys — a deliberate difference. The helpers `eel_zip/2`,
  `eel_enumerate/2` are generated on demand; the function and helper names are reserved. In a
  comprehension head they are ordinary generators: `for i, x in enumerate(xs)`,
  `for a, b in zip(xs, ys)`.
- `range(stop)`, `range(start, stop)`, `range(start, stop, step)` — Python semantics, eager: it returns
  the list `[start, start+step, …]` without `stop`. Integers only: a non-number is
  `{bad_argument, range, V}`; `step == 0` is `{bad_argument, range, 0}` (Python's ValueError). The
  `eel_range/3` helper (start, stop, step) is recursive: `lists:seq` does not fit — it crashes on
  "empty" ranges (`lists:seq(5, 0, 1)`, `lists:seq(1, 9, -1)`) where Python gives `[]`. In a
  comprehension head it works as an ordinary generator: `for i in range(10)`.
- In a module, printing uses the generated on-demand helpers `eel_print/1` and `eel_str/1` (plus
  `eel_str_tuple/1`, `eel_str_map/1`); the names are reserved. In the notebook the printed text is
  delivered before the cell's value, and a `nil` value is not printed (as `None` in IPython).

## The compiler

```
src/a/b.eel ──ast.parse──▶ Python AST ──▶ IR ──▶ checks ──▶ abstract forms ──compile:forms──▶ .beam
```

- **Frontend** — the stock `ast.parse` from CPython 3.15. Parsing and positions come for free;
  `compile()` is not called, so Python's own checks do not get in the way of our semantics.
- **IR** — our own representation (structs carrying the `.eel` line). It corresponds to Gleam's
  `TypedExpr`/`TypedModule` layer (`compiler-core/src/ast/typed.rs` — the tree the code generator
  walks), but **without types**: checks take the place of inference. This is safe for parity: Gleam's
  Erlang generator barely reads the inferred types (only when printing `-spec`); the shape of the code
  is chosen by the node variant — that is, by the syntax. Eel therefore puts no type information into
  the forms: annotations stay documentation and `-spec`/`-type` are not generated. The node set follows
  the `TypedExpr` categories so that the backend walk repeats Gleam's generator. The frontend is
  invisible behind the IR: when syntax beyond Python arrives, only the frontend is replaced and the
  backend stays.
- **Error messages are English** (`eel: path:line: text`, lowercase, no trailing period); so are the
  `compile:forms` errors after parsing them in the escript.
- **Checks** are split: the frontend rejects forbidden syntax (`for`/`while`/`del`/mutation, a node
  whitelist), the backend checks "the branch ends with an assignment to the same variable", and
  `checks.py` — duplicate definitions.
- **Backend** — like Gleam 1.19's `erlang-generation`: a builder on top of the Erlang Abstract Format,
  where every form carries the `.eel` line annotation, so stack traces point into the eel source. Forms
  are handed to Erlang as an **ETF blob** (`eel/etf.py`) rather than as text: `binary_to_term/1` does
  not parse a grammar, so decoding is an order of magnitude cheaper than `file:consult` (measured on
  40 modules: 79 ms versus 5 ms). Readable `.abstr` text is written only with the `--dump-forms` flag.
  The `compile` attribute repeats Gleam's (`no_auto_import, nowarn_ignored, nowarn_unused_vars,
  nowarn_unused_function, nowarn_nomatch, inline`).
- **Build** — `eel_compile.escript` runs as a **server**: it reads `{EtfPath, ErlFlag}.` requests from
  stdin and answers `OK` / `ERR <base64 of the text>`. The forms are one ETF blob
  `[{Base, Forms, SourcePath}, …]` (`_eel_artefacts/forms.etf`); `compile:forms` is called with
  `[return, debug_info, {source, Path}]` (no `report_*`: stdout is the protocol channel). The `.beam`
  goes into ebin and `<app>.app` is generated next to it; `.erl` only with the `--erl` flag (`erl_pp`
  of the same forms). `CompilerProcess` in Python keeps one escript per process and reuses it across
  builds: a repeated build does not start an Erlang VM again (measured: 1.1 s → 0.45 s). A one-shot CLI
  gains nothing from the reuse — there is one VM start per invocation.
- **Compiler daemon** — a background escript that survives CLI invocations: the first build starts it,
  after which commands only send requests over the unix socket
  `$XDG_CACHE_HOME/eel/compiler-<sha1(escript path)>.sock` (with `.pid` and `.log` next to it; the key
  is the escript path, so different installations get their own daemons). Measured on 40 modules: the
  first build 794 ms (VM start), afterwards 307 ms versus 672 ms with no daemon; a single module —
  124 ms versus 596 ms. Requests are Erlang terms, one per line: `{compile, EtfPath, ErlFlag}`,
  `{ping}`, `{stop}`; answers are `OK` / `ERR <base64>`. It lives until `eel daemon stop` is run
  (there is no idle timeout). The protocol version is verified: on a mismatch the daemon is restarted;
  a stale socket is removed. `--no-daemon` and an unreachable daemon → a silent fallback to an escript
  inside the process; the build never fails because of it.
- **Incremental build** — only changed modules are compiled: the manifest `_eel_artefacts/build.json`
  holds `{format, builder, modules: {name: sha256}}`, where `builder` is the hash of the compiler's own
  sources (not a hand-maintained version). A module is rebuilt when its source hash differs, its
  `.beam` is missing, or a requested artefact (`--erl`/`--dump-forms`) is missing; the hash is over
  content, not mtime, so `git checkout` and `touch` cause no false rebuilds. Removed `.eel` files drop
  their `.beam`/`.erl`/`.abstr` from the output. When there is nothing to compile, no request is sent
  to the daemon at all. The manifest is written only after a successful compilation; a corrupt or
  foreign one (another `format`/`builder`) means a full rebuild. `--force` rebuilds everything.
  Measured (40 modules, daemon up): 119 ms with no changes, 120 ms after editing one module, 326 ms
  with `--force`.
- **Build server** — a live **Python** process (`python -m eel.server`) that accepts builds over the
  unix socket `$XDG_CACHE_HOME/eel/build-<sha1(install path)>.sock`; the protocol is JSON lines
  `{"op": "build"|"ping"|"stop"}` (both server and client are Python — Erlang terms would be out of
  place here). Nothing watches the filesystem: a build happens only on request, but it does not pay
  for interpreter startup or the compiler import. `eel build` first goes through a **thin client**
  (`eel/client.py`: socket/json/pathlib, no compiler import) and only compiles inside the process when
  the server is unreachable; the server starts automatically on the first build.
  **The lifetimes are tied**: the server starts the compiler daemon immediately at startup (not on the
  first build) and stops it on any exit — on `{stop}`, on `SIGTERM`/`SIGINT`, and in the server's
  `finally`; the escript additionally watches its parent through stdin EOF (the server keeps the pipe
  open), so the daemon dies even if the server is killed with `SIGKILL`. A library `build()` does
  **not** start the daemon (`CompilerDaemon.compile(autostart=False)`): with no live daemon the build
  runs an escript inside the process. This way the compiler daemon lives exactly as long as the build
  server. `--no-daemon` means no background processes at all.
  Measured (40 modules): bare `python -c pass` 25 ms; python startup plus client import 60–70 ms; a
  CLI call with a live server 70–85 ms; a socket request 7–8 ms; a build after editing one module over
  the socket 4–6 ms. Hence the rule: the "edit → build" loop should go through the socket, not through
  a new process.

Parity with Gleam is mandatory only at the lowest layer — the abstract format: the builder repeats
`erlang-generation`, and for identical syntax nodes eel builds the same forms as Gleam; above that
(Python AST, IR) the layers are our own.

## Command line

`python -m eel build [DIR]` — subcommands as in Gleam; the first release knows only `build`. It
compiles the `src/` tree into `build/dev/erlang/<app>/ebin`, where `<app>` is the project directory
name; the artefacts (the ETF blob and, by flag, `.abstr`/`.erl`) go into
`build/dev/erlang/<app>/_eel_artefacts`.

## Jupyter

A notebook without a custom kernel: a cell stays Python text (autocomplete and linting come from the
Python LSP), while execution is intercepted by the `eel.notebook` ipykernel hook: `ast_transformer`
replaces the body with `pass`, and `post_run_cell` compiles the source in shell mode and runs it in a
persistent Erlang REPL.

- **Shell mode** (`session.py`): a cell → Erlang expressions (not a module); `def` → a named fun
  `F = fun F(X) -> … end` (recursion works); `_taken`/`_scope`/classes/imports live in the session
  (the `X@N` mapping continues across cells). Re-running a cell is safe: classes are redefined and a
  rebinding yields a new name `X@N`.
- **REPL** (`repl.py` + `repl.escript`): a persistent process; `erl_eval:exprs` takes forms directly
  and bindings live between cells; the output is the result of the last expression (definitions
  produce none). The helpers `_eel_pow`/`eel_iter` are pre-bound in the REPL as fun values, and shell
  mode calls them through a variable (in a module build they are local functions). Before the result
  of every comprehension its Erlang equivalent is printed
  (`erl: [X * 2 || X <- eel_iter(L)]`) — `erl_pp` of the corresponding form; comprehension targets get
  local names (`X`, not `X@N`).

## Parity with Gleam

- The goal document requires identical generated code. The mechanism: an installed `gleam` 1.19.2 (the
  same version as in the local `gleam/` clone, which is not part of the repository) compiles an
  equivalent `.gleam` module; the beam carries abstract code (`debug_info`), the forms come from
  `beam_lib:chunks(Beam, [abstract_code])` and are compared with eel's forms after normalising line
  annotations.
- 10–20 such tests (pytest); one test is a pair of equivalent eel/gleam modules.
- Not every construct has a pair: the `|=` merge, `.items()`, a non-exhaustive match and comprehensions
  have no Gleam counterpart — their behaviour is covered by plain tests.
- Match pairs are exhaustive only: Gleam adds a fallthrough clause that eel deliberately does not have.
