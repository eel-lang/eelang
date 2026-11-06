"""ETF (External Term Format): eel forms → a binary for Erlang.

Replaces the textual `.abstr` + `file:consult`: `binary_to_term/1` reads tags instead of
parsing a grammar, so decoding is an order of magnitude cheaper (see specs/architecture.md, "The compiler").

An important detail: a Python `str` in the forms is a **charlist**, not a binary. In the forms strings
live in `{string, L, "chars"}` and `{file, L, "path"}`, and `compile:forms` expects a list of codes there.
"""

from __future__ import annotations

import struct
import zlib

from .term import Atom

VERSION = 131

NEW_FLOAT_EXT = 70
SMALL_INTEGER_EXT = 97
INTEGER_EXT = 98
SMALL_BIG_EXT = 110
ATOM_UTF8_EXT = 118
SMALL_ATOM_UTF8_EXT = 119
SMALL_TUPLE_EXT = 104
LARGE_TUPLE_EXT = 105
NIL_EXT = 106
LIST_EXT = 108
COMPRESSED = 80

INT32_MIN, INT32_MAX = -(2**31), 2**31 - 1


def encode(term, compress: bool = True) -> bytes:
    """A term → an ETF blob (with ``131`` in front), zlib-compressed when that pays off."""
    body = bytearray()
    _encode(body, term)
    raw = bytes(body)
    if compress:
        packed = zlib.compress(raw, 6)
        if len(packed) < len(raw):
            return bytes([VERSION, COMPRESSED]) + struct.pack(">I", len(raw)) + packed
    return bytes([VERSION]) + raw


def _atom(out: bytearray, name: str) -> None:
    encoded = name.encode("utf-8")
    if len(encoded) < 256:
        out += bytes([SMALL_ATOM_UTF8_EXT, len(encoded)])
    else:
        out += bytes([ATOM_UTF8_EXT]) + struct.pack(">H", len(encoded))
    out += encoded


def _integer(out: bytearray, value: int) -> None:
    if 0 <= value < 256:
        out += bytes([SMALL_INTEGER_EXT, value])
    elif INT32_MIN <= value <= INT32_MAX:
        out += bytes([INTEGER_EXT]) + struct.pack(">i", value)
    else:  # arbitrary precision: length, sign, little-endian bytes
        sign = 1 if value < 0 else 0
        magnitude = -value if value < 0 else value
        raw = magnitude.to_bytes((magnitude.bit_length() + 7) // 8 or 1, "little")
        out += bytes([SMALL_BIG_EXT, len(raw), sign]) + raw


def _encode(out: bytearray, term) -> None:
    if isinstance(term, Atom):
        _atom(out, str(term))
    elif isinstance(term, int):
        _integer(out, term)
    elif isinstance(term, float):
        out += bytes([NEW_FLOAT_EXT]) + struct.pack(">d", term)
    elif isinstance(term, str):
        out += bytes([LIST_EXT]) + struct.pack(">I", len(term))
        for char in term:
            _integer(out, ord(char))
        out += bytes([NIL_EXT])
    elif isinstance(term, list):
        out += bytes([LIST_EXT]) + struct.pack(">I", len(term))
        for item in term:
            _encode(out, item)
        out += bytes([NIL_EXT])
    elif isinstance(term, tuple):
        if len(term) < 256:
            out += bytes([SMALL_TUPLE_EXT, len(term)])
        else:
            out += bytes([LARGE_TUPLE_EXT]) + struct.pack(">I", len(term))
        for item in term:
            _encode(out, item)
    else:
        raise TypeError(f"cannot encode {type(term).__name__} as an Erlang term")
