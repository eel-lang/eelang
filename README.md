# Eel

A Gleam-like language for the BEAM (the Erlang VM) with Python syntax.
Sources are `.eel` files; the compiler emits the Erlang Abstract Format and `.beam` files the
same way Gleam 1.19 does.

## Installation

```
pip install eelang      # the language is eel, but PyPI's "eel" belongs to another library
```

The package needs Python 3.15 and Erlang/OTP with `escript` in `PATH`. After installation both
`eel build DIR` and `python -m eel build DIR` are available.

## Quick start

A project is a directory with a `src/` tree:

```
my_app/
└── src/
    └── hello.eel
```

`hello.eel`:

```python
def hello():
    "hi"
```

Build:

```
python -m eel build my_app            # .beam into build/dev/erlang/my_app/ebin
python -m eel build my_app --erl      # also write .erl next to every .beam
eel daemon stop                       # stop the background compiler
```

Builds are incremental: only changed modules are recompiled (content hash, not mtime), and removed
`.eel` files drop their `.beam`; `--force` rebuilds everything.

The background part has two layers: a **build server** (a live Python process accepting builds over
a unix socket) that holds the **compiler daemon** (an Erlang VM). Nothing watches the filesystem —
builds happen only on request, but without paying for interpreter and VM startup: editing one module
in a 40-module project costs 4–6 ms over the socket, 70–85 ms through a plain `eel build` (the rest
is CPython startup). A custom integration (editor, LSP, hook) can talk to the socket directly:
`from eel import client; client.build_remote(path)`.

Both processes live together: `eel daemon start` brings up the server and the compiler daemon,
`eel daemon stop` shuts both down, and if the server is killed (even with `kill -9`) the compiler
daemon leaves on its own — it watches its parent through stdin closing. `--no-daemon` builds
in-process with no background processes at all.

Run it:

```
erl -pa my_app/build/dev/erlang/my_app/ebin -noshell \
  -eval 'io:format("~p~n", [hello:hello()]), halt().'
# => <<"hi">>
```

A module is the path of its file under `src/`: `src/a/b/c.eel` → module `a@b@c`; the import
`from a.b.c import f` becomes a call to `a@b@c:f(...)`.

## Language

Python syntax, Gleam/Erlang semantics:

- immutability: no `for`/`while` loops, no mutation; `x = x + 1` is a rebinding
  (a new Erlang name `X@1`);
- atoms use single quotes `'ok'`, strings use double quotes `"..."`;
- `if`/`else` and `match`/`case` (the condition must be a bool; an unmatched match is "let it crash");
- classes `class T(AtomPrefixed): x: int` compile to a tuple `{t, …}`;
- `lambda`, list/dict comprehensions, decorators;
- processes: `match receive(): case …` (selective receive), `case Timeout(ms):` for the timeout,
  `send(pid, msg)`, `self()`, `spawn(lambda: …)`;
- `print(x)` writes to stdout (Python-like value display) and returns `nil`;
- `zip(a, b)` yields pairs up to the shorter list, `enumerate(xs[, start])` yields `(index, value)`
  pairs; both are eager and accept lists only;
- `range(...)` is Python's range, but eager: it returns a list;
- `print`/`zip`/`enumerate`/`range` can be redefined with your own `def` — names resolve like any
  other name (by name);
- standard-library methods: `d.keys()/values()/items()/get()`, `s.startswith/endswith/
  lower/upper/strip/lstrip/rstrip/split/join/replace/splitlines`, `xs.count(x)` on lists and tuples;
- `==` behaves like Python (`1 == 1.0` → true), `// % **` follow Python semantics.

The full rules live in `specs/goal-and-requirements.md` and `specs/architecture.md`.
Examples — `eel.ipynb`: a stage-by-stage tour of the language.

## Development

```
python -m venv .venv
.venv/bin/pip install pytest
.venv/bin/python -m pytest
```

The suite includes 18 parity pairs with Gleam: for equivalent code eel and Gleam 1.19.2 produce
identical Erlang forms. They need a `gleam` binary in `PATH` (the Gleam compiler itself is not part
of this repository — keep a local clone in `gleam/` for reference, it is in `.gitignore`).
