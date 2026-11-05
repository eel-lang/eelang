# Eel

Gleam-подобный язык для BEAM (виртуальной машины Erlang) с синтаксисом Python.
Исходники — файлы `.eel`; компилятор генерирует Erlang Abstract Format и `.beam` так же,
как Gleam 1.19.

## Установка

```
pip install eelang      # язык называется eel, но на PyPI имя «eel» занято другой библиотекой
```

Пакет требует Python 3.15 и Erlang/OTP с `escript` в `PATH`. После установки доступны
`eel build DIR` и `python -m eel build DIR`.

## Быстрый старт

Проект — каталог с деревом `src/`:

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

Сборка:

```
python -m eel build my_app            # .beam в build/dev/erlang/my_app/ebin
python -m eel build my_app --erl      # ещё и .erl рядом с каждым .beam
eel daemon stop                       # остановить фоновый компилятор
```

Сборка инкрементальная: пересобираются только изменившиеся модули (хэш содержимого, а не mtime),
удалённые `.eel` убирают свои `.beam`; `--force` пересобирает всё.

Фоновая часть двухслойная: **сервер сборки** (живой Python-процесс, принимает сборки по unix-сокету)
держит внутри **демон компилятора** (Erlang-VM). Слежения за файлами нет — сборка идёт только по
запросу, но без старта интерпретатора и VM: правка одного модуля в проекте из 40 — 4–6 мс по сокету,
70–85 мс обычным `eel build` (остальное — старт CPython). Своя интеграция (редактор, LSP, хук)
может говорить с сокетом напрямую: `from eel import client; client.build_remote(path)`.
Оба процесса живут вместе: `eel daemon start` поднимает сервер и демон компилятора,
`eel daemon stop` гасит оба, а если сервер убить (даже `kill -9`), демон компилятора уходит сам —
он стережёт родителя по закрытию stdin. `--no-daemon` собирает в процессе без фоновых процессов.

Первая сборка поднимает фоновый демон компилятора (Erlang-VM), и он живёт, пока не выполнят
`eel daemon stop` — следующие сборки не тратят ~0.5 с на старт VM. `--no-daemon` собирает
внутри процесса, `eel daemon status` показывает pid, версию протокола и число сборок.

Запуск:

```
erl -pa my_app/build/dev/erlang/my_app/ebin -noshell \
  -eval 'io:format("~p~n", [hello:hello()]), halt().'
# => <<"hi">>
```

Модуль — это путь файла под `src/`: `src/a/b/c.eel` → модуль `a@b@c`; импорт
`from a.b.c import f` → вызов `a@b@c:f(...)`.

## Язык

Синтаксис Python, семантика Gleam/Erlang:

- неизменяемость: циклов `for`/`while` нет, мутация запрещена; `x = x + 1` — переприсваивание
  (новое Erlang-имя `X@1`);
- атомы — одинарные кавычки `'ok'`, строки — двойные `"..."`;
- `if`/`else` и `match`/`case` (в условии — только bool; несматченный match — «let it crash»);
- классы `class T(StartingWithAtom): x: int` → кортеж `{t, …}`;
- `lambda`, list/dict comprehensions, декораторы;
- процессы: `match receive(): case …` (селективный приём), `case Timeout(ms):` — таймаут,
  `send(pid, msg)`, `self()`, `spawn(lambda: …)`;
- `print(x)` — печать в stdout (питоновское представление значений), возвращает `nil`;
- `zip(a, b)` — пары до конца короткого списка, `enumerate(xs[, start])` — пары `(номер, значение)`;
  оба неленивые и принимают только списки;
- `range(...)` — питоновский range, но неленивый: возвращает список;
- `print`/`zip`/`enumerate`/`range` можно переопределить своим `def` — имя разрешается как обычное (по имени);
- методы стандартной библиотеки: `d.keys()/values()/items()/get()`, `s.startswith/endswith/
  lower/upper/strip/lstrip/rstrip/split/join/replace/splitlines`, `xs.count(x)` у списков и кортежей;
- `==` как в Python (`1 == 1.0` → true), `// % **` — Python-семантика.

Полные правила — в `specs/goal-and-requirements.md` и `specs/architecture.md`.

## Разработка

```
python -m venv .venv
.venv/bin/pip install pytest
.venv/bin/python -m pytest
```

Тесты включают 18 паритет-пар с Gleam: на эквивалентном коде eel и Gleam 1.19.2 генерируют
одинаковые Erlang-формы. Для них нужен установленный `gleam` в `PATH` (сам компилятор Gleam в
репозиторий не входит — для справки держите локальный клон `gleam/`, он в `.gitignore`).
