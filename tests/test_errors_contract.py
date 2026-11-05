"""Error contract: Erlang lint errors surface as `path:line: text`, never as a term dump."""

from __future__ import annotations

import pytest

from squeal import etf
from squeal.compiler import COMPILER
from squeal.errors import SquealError
from squeal.term import Atom


def test_lint_error_is_clean(tmp_path):
    # a form calling a missing local function — the linter rejects it
    forms = [
        (Atom("attribute"), 0, Atom("module"), Atom("t")),
        (Atom("attribute"), 0, Atom("export"), [(Atom("m"), 0)]),
        (Atom("attribute"), 0, Atom("file"), ("src/t.ee", 0)),
        (Atom("function"), 2, Atom("m"), 0, [
            (Atom("clause"), 2, [], [],
             [(Atom("call"), 3, (Atom("atom"), 3, Atom("nope")), [])]),
        ]),
    ]
    blob = tmp_path / "t.etf"
    blob.write_bytes(etf.encode([(str(tmp_path / "t"), forms, "src/t.ee")]))

    with pytest.raises(SquealError) as exc:
        COMPILER.compile(blob, False)
    assert str(exc.value) == "src/t.ee:3: function nope/0 is not defined"


def test_write_failure_is_clean_and_survivable(tmp_path):
    forms = [
        (Atom("attribute"), 0, Atom("module"), Atom("t")),
        (Atom("attribute"), 0, Atom("export"), [(Atom("m"), 0)]),
        (Atom("function"), 1, Atom("m"), 0, [(Atom("clause"), 1, [], [], [(Atom("integer"), 1, 1)])]),
    ]
    blob = tmp_path / "t.etf"
    blob.write_bytes(etf.encode([(str(tmp_path / "missing" / "t"), forms, "src/t.ee")]))

    with pytest.raises(SquealError, match="cannot write .*\\.beam"):
        COMPILER.compile(blob, False)
    # the compiler process survives a failed write and stays usable
    ok = tmp_path / "ok.etf"
    ok.write_bytes(etf.encode([(str(tmp_path / "t"), forms, "src/t.ee")]))
    COMPILER.compile(ok, False)
    assert (tmp_path / "t.beam").exists()
