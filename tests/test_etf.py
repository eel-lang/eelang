"""ETF-кодировщик: типы, round-trip через Erlang и оракул «формы совпадают с текстом»."""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

from squeal import etf
from squeal.compiler import compile_source
from squeal.term import Atom, term_to_erlang

ERL = shutil.which("erl")


def _decode(tmp_path: pathlib.Path, term) -> str:
    blob = tmp_path / "t.etf"
    blob.write_bytes(etf.encode(term))
    script = (
        f'{{ok, B}} = file:read_file("{blob}"), T = binary_to_term(B), '
        'io:format("~w", [T]), halt().'
    )
    out = subprocess.run(
        [ERL, "-noshell", "-eval", script], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def test_round_trip_types(tmp_path):
    term = [
        (
            Atom("ok"), 1, 255, -1, 1099511627776,
            2.5, 1e-5, "привет", [], (Atom("x"),), [1, [2]],
        )
    ]
    blob = tmp_path / "t.etf"
    blob.write_bytes(etf.encode(term))
    script = (
        f'{{ok, B}} = file:read_file("{blob}"), '
        '[{ok, 1, 255, -1, Big, F, Tiny, S, Empty, Tup, Nested}] = binary_to_term(B), '
        'io:format("~p", [{Big =:= 1099511627776, F =:= 2.5, Tiny =:= 1.0e-5, '
        'S =:= "привет", Empty =:= [], Tup =:= {x}, Nested =:= [1, [2]]}]), halt().'
    )
    out = subprocess.run(
        [ERL, "-noshell", "-eval", script], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "{true,true,true,true,true,true,true}"


def test_strings_are_charlists_not_binaries(tmp_path):
    """В формах `str` — charlist: `{string, L, "chars"}` иначе сломает compile:forms."""
    assert _decode(tmp_path, "ab") == "[97,98]"
    assert _decode(tmp_path, "") == "[]"


def test_big_integers(tmp_path):
    assert _decode(tmp_path, 10**30) == "1000000000000000000000000000000"
    assert _decode(tmp_path, -(10**30)) == "-1000000000000000000000000000000"


def test_unsupported_type_is_loud():
    with pytest.raises(TypeError):
        etf.encode([object()])


def test_forms_from_etf_equal_text_forms(tmp_path):
    """Оракул: `binary_to_term(ETF)` =:= `file:consult(текстовые формы)`."""
    source = (
        "class T(StartingWithAtom):\n"
        "    a: int\n"
        "    b: int\n"
        "\n"
        "def f(xs, d):\n"
        "    if xs:\n"
        "        r = [x * 2 for x in xs if x > 1]\n"
        "    else:\n"
        "        r = []\n"
        '    (r, "привет", 2.5, 1000000000000000000000000000000, d, T(1, 2))\n'
    )
    forms = compile_source(source, "src/m.ee", "m")
    text = tmp_path / "m.abstr"
    text.write_text("".join(term_to_erlang(form) + ".\n" for form in forms))
    blob = tmp_path / "m.etf"
    blob.write_bytes(etf.encode(forms))

    script = (
        f'{{ok, FromText}} = file:consult("{text}"), '
        f'{{ok, Raw}} = file:read_file("{blob}"), '
        'io:format("~p", [binary_to_term(Raw) =:= FromText]), halt().'
    )
    out = subprocess.run(
        [ERL, "-noshell", "-eval", script], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "true"
