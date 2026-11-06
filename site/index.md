---
title: Eel — Python's syntax, Erlang's semantics
description: Eel compiles .eel files straight to the BEAM: Python's syntax with Gleam's model — immutable by default, no loops, let it crash.
brand: Eel
status: Status: Alpha
github: https://github.com/pwtail/squeal
footer: Eel · Python syntax, Erlang semantics.
---

<!--
  Source of truth for site/index.html. Редактируй текст здесь, HTML собирается из этого файла.

  Как читать разметку:
  - `_курсив_` в заголовке (H1/H2) — серая подсветка, как вторая строка «Built on Gleam, …».
  - `<br>` — принудительный перенос строки внутри заголовка.
  - `###` — блок внутри секции: квадрат из шести принципов, таб в обзоре синтаксиса,
    плитка в stdlib-сетке, пункт плюсов/минусов.
  - Блок кода с первой строкой-подписью (`### Values · values.eel`) — панель кода с именем файла.
  - `#якорь` в ссылке — id секции; ссылки в футере строятся из H2-секций, текст — из `footer:`.
-->

# A _Python-shaped_ language for the _BEAM_.

Immutability, pattern matching, comprehensions and hierarchical modules — written in the Python
you already know, compiled to Erlang abstract forms through the same pipeline Gleam uses.

Built on: Erlang/OTP 29 · CPython 3.15 · Inspired by: Gleam

[Read the syntax overview →](#syntax)

### fib.eel
_one function with a match, instead of Erlang's multiple function clauses_

```python
def fib(n, current, next):
    match n:
        case 0:
            current

        case _ if n > 0:
            fib(n - 1, next, current + next)

def fib(n):
    fib(n, 0, 1)
```

## Eel's objectives

Eel is inspired by Gleam, but its goals and founding principles differ in a few important ways.

### 01 · Great IDE support
_static analysis, first-class_

A great static-analysis experience in the IDE is one of the goals — and the main target is PyCharm.

In its first version Eel is almost plain Python, so syntax highlighting and autocomplete work
before any Eel-specific tooling exists.

### 02 · Great stdlib
_batteries, with methods_

A stdlib that is pleasant to use, with OOP-style methods on the core data structures.

```text
dic.get(my_key)
my_list.map(my_func)
name.startswith(prefix)
```

### 03 · Optional static typing
_never mandatory_

Static typing is fully optional. When you reach for it, it is there to make the code clearer and
more expressive.

### 04 · All data types equally supported
_no second-class data_

Maps, records, native records and the rest get the same level of support — and optional types make
all of them convenient.


### 05 · OTP first-class
_not an afterthought_

Working with OTP matters as much as a good standard library.

### 06 · Python-friendly
_BEAM ↔ CPython_

One of the tasks is to bring the BEAM and CPython ecosystems closer, and to enable projects
like Hornbeam.

## The syntax you need,<br>_baked into Python._

Every construct below is compiled, not interpreted: the compiler turns this Python into Erlang
abstract forms. Pick a category and read the code.

### Values · values.eel
Quotes decide the type. Tuples, lists and dicts keep their Erlang shapes.

```python
def pair():
    ('ok', "ok", (1, 2), [1, 2], {'k': 1})
# {ok, <<"ok">>, {1, 2}, [1, 2], #{k => 1}}
```

### Operators · operators.eel
Arithmetic keeps Python's semantics; booleans compile to `andalso` and `orelse`.

```python
def calc(a, b):
    (a // b, a % b, a ** 2, a == b, not (a < b))
```

### Control flow · control.eel
`match` is an expression: every branch assigns the same variable.

```python
def classify(n):
    match n:
        case 0:
            r = 'zero'
        case _ if n < 0:
            r = 'negative'
        case _:
            r = 'positive'
    r
```

### Patterns · patterns.eel
Destructuring, constructor patterns and guards compile to real Erlang patterns.

```python
def first_two(xs):
    a, b, *rest = xs
    (a, b)

def swap(p):
    match p:
        case (x, y):
            q = (y, x)
    q
```

### Functions · functions.eel
`def`, `lambda` and closures — and arity is part of a function's identity.

```python
def twice(f, x):
    f(f(x))

def bump(n):
    twice(lambda x: x + 1, n)
```

### Classes · classes.eel
A class compiles to a tagged tuple, and its constructor is inlined.

```python
class Point(StartingWithAtom):
    x: int
    y: int

def shift(p, dx):
    match p:
        case Point(x, y):
            q = Point(x + dx, y)
    q
```

### Comprehensions · comprehensions.eel
List and dict comprehensions become Erlang comprehensions, filters included.

```python
def evens(xs):
    [x * 2 for x in xs if x % 2 == 0]

def by_id(pairs):
    {k: v for k, v in pairs}
```

### Modules · modules.eel
The module is the path; imports are Python imports with Gleam's rules.

```python
from my.app.math import add, mul as times

def total(a, b):
    times(add(a, b), 2)
```

## OOP-styled _standard library_
_planned surface · not in the compiler yet_

### Strings
- `name.startswith(prefix)` — prefix test, bytes included
- `name.strip()` — trim whitespace on both ends
- `line.split(sep)` — split into a list of strings
- `path.replace(from, to)` — replace every occurrence
- `sep.join(items)` — join a list back into one string

### Lists
- `xs.map(f)` — apply a function to every element
- `xs.filter(pred)` — keep the elements that match
- `xs.fold(acc, f)` — reduce to a single value
- `xs.take(n)` — the first n elements
- `xs.zip(ys)` — pairs, like Python's zip

### Maps
- `config.get(key)` — value or nil, never an exception
- `m.put(key, value)` — a new map, the old one intact
- `m.merge(other)` — what `|=` compiles to
- `m.keys()` — keys, in no particular order
- `m.items()` — pairs, for comprehensions

## This is an early alpha.<br>_100% vibe-coded._

It compiles real modules today, and it is not something you put in production yet. The honest list
of what that buys you and what it costs:

### Plus
- **A small codebase you can fork** — the compiler is a handful of Python modules: read it in an
  afternoon, fork it, and bend it to your own project.

### Minus
- **No type checker** — annotations are documentation. Eel does not check them; outside tools own
  that, as in Python.
- **No static analysis yet** — the IDE experience is a goal, not a feature: the analyzer is planned,
  and PyCharm support does not exist today.
- **The standard library is a sketch** — OOP-style methods on the core types are designed, not
  implemented; today you work with bare Erlang forms.
- **Pre-release, nothing to install** — runs from a checkout on Python 3.15 and OTP 29: no package,
  no versioned releases, no stability promise.
- **Written with AI, end to end** — expect uneven code, thin tests in places and APIs that move. The
  parity suite with Gleam is the part that keeps it honest.
