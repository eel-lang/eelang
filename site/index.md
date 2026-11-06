---
title: Eel — Python's syntax, Erlang's semantics
description: Eel compiles .eel files straight to the BEAM: Python's syntax with Gleam's model — immutable by default, no loops, let it crash.
brand: Eel
status: Status: Alpha
github: https://github.com/squea-lang/eel
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
_compiled · one function with a match instead of Erlang's multiple clauses_

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
sep.join(lines)
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

## The syntax is almost Python

Every construct below is compiled, not interpreted: the compiler turns this Python into Erlang
abstract forms. Pick a category and read the code.

### Values · values.eel
Numbers, floats, strings, atoms, booleans and `None`. Lists, tuples and dicts keep their Erlang shapes.

```python
def values():
    (42, 1.5, "hi", 'ok', True, False, None)


def collections():
    ([1, 2, 3], (1, 2), {'a': 1}, {"b": 2})
```

### Operators · operators.eel
Python semantics for `//`, `%`, `**` and `==`; booleans compile to `andalso` and `orelse`.

```python
def ops():
    (7 // 2, -7 // 2, -7 % 2, 2 ** 10, 1 == 1.0, "a" < "b")
```

### Control flow · control.eel
`if` is true only for `true`; `match` handles or-patterns, guards and a wildcard.

```python
def branch(n):
    match n:
        case 1 | 2:
            r = 'one or two'
        case big if big > 10:
            r = 'big'
        case _:
            r = 'other'
    r


def compare(a, b):
    if a < b:
        r = 'yes'
    else:
        r = 'no'
    r
```

### Patterns · patterns.eel
Destructuring with a star, and list patterns inside `match`.

```python
def head_tail(xs):
    head, *rest = xs
    (head, rest)


def only_head(xs):
    match xs:
        case [h, *t]:
            r = (h, t)
    r
```

### Functions · functions.eel
`def`, `lambda` and closures — and arity is part of a function's identity.

```python
def twice(f, x):
    f(f(x))


def inc(n):
    twice(lambda x: x + 1, n)
```

### Decorators · decorators.eel
Applied once, like Python — a chain wraps from the inside out.

```python
def plus1(fn):
    lambda x: fn(x) + 1


@plus1
@plus1
def z(x):
    x


def run():
    z(3)
```

### Classes · classes.eel
A class becomes a tagged tuple, and its constructor is inlined.

```python
class Point(AtomPrefixed):
    x: int
    y: int


def shift(p, dx):
    match p:
        case Point(x, y):
            q = Point(x + dx, y)
    q
```

### Comprehensions · comprehensions.eel
Nested generators, filters, and dict comprehensions through `.items()`.

```python
def grid():
    [x + y for x in [1, 2] for y in [10, 20]]


def doubled(m):
    {k: v * 2 for k, v in m.items()}
```

### Modules · modules.eel
The module is the path; imports are Python imports with Gleam's rules.

```python
from a.b.c import helper


def call():
    helper()
```

## OOP-styled _standard library_

OOP-styling is an exotic feature of Eel: it makes the standard library pleasant to use, and it
complicates nothing — outside the standard library, OOP is not allowed anywhere.

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

It compiles real modules today, however it is not something you put in production yet. Also, the code
was written mostly with DeepSeek 4 Flash, in a vibe-coding loop.

### Plus
- **A small codebase you can fork** — the compiler is a handful of Python modules: read it in an
  afternoon, fork it, and bend it to your own project.

### Minus
- **Everything else is a minus.**
