"""Message passing: receive (selective), the Timeout clause, send/self/spawn."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

import pytest

from eel.compiler import compile_source
from eel.errors import EelError
from eel.term import Atom

ERL = shutil.which("erl")
ROOT = pathlib.Path(__file__).resolve().parent.parent


def _body(source: str, name: str = "m"):
    forms = compile_source(source, "src/m.eel", "m")
    fn = next(f for f in forms if f[0] == Atom("function") and f[2] == Atom(name))
    return fn[4][0][4][0]


def test_receive_value_form():
    body = _body("def m():\n    receive()\n")
    assert body[0] == Atom("receive")
    assert body[2][0][2] == [body[2][0][2][0]]        # clause [V] -> [V]
    assert body[2][0][3] == []
    assert body[2][0][4] == [body[2][0][2][0]]


def test_selective_receive_form():
    body = _body(
        "def m():\n"
        "    match receive():\n"
        "        case Ok(v):\n"
        "            v\n"
        "        case _:\n"
        "            0\n"
    )
    assert body[0] == Atom("receive")
    clauses = body[2]
    assert len(clauses) == 2
    assert clauses[0][2] == [
        (Atom("tuple"), 3, [(Atom("atom"), 3, Atom("ok")), (Atom("var"), 3, Atom("V"))])
    ]
    assert clauses[1][2] == [(Atom("var"), 5, Atom("_"))]


def test_timeout_clause_becomes_after():
    body = _body(
        "def m():\n"
        "    match receive():\n"
        "        case Ok(v):\n"
        "            v\n"
        "        case Timeout(50):\n"
        "            'slow'\n"
    )
    assert body[0] == Atom("receive") and len(body) == 5
    assert body[3] == (Atom("integer"), 5, 50)         # after 50 ->
    assert body[4] == [(Atom("atom"), 6, Atom("slow"))]


def test_send_self_spawn_forms():
    body = _body("def m(pid, msg):\n    send(pid, msg)\n")
    assert body[2] == (Atom("remote"), 2, (Atom("atom"), 2, Atom("erlang")),
                       (Atom("atom"), 2, Atom("!")))

    body = _body("def m():\n    self()\n")
    assert body[2][3] == (Atom("atom"), 2, Atom("self"))

    body = _body("def m():\n    spawn(lambda: 1)\n")
    assert body[2][3] == (Atom("atom"), 2, Atom("spawn"))
    assert body[3][0][0] == Atom("fun")


def test_errors():
    cases = [
        ("def m():\n    receive(1000)\n", "receive takes no arguments"),
        (
            "def m():\n"
            "    match receive():\n"
            "        case Timeout(5):\n"
            "            0\n"
            "        case _:\n"
            "            1\n",
            "Timeout clause must be the last one",
        ),
        (
            "def m():\n"
            "    match receive():\n"
            "        case Timeout(5) if True:\n"
            "            0\n",
            "Timeout clause takes no guard",
        ),
        (
            "def m(x):\n    match x:\n        case Timeout(5):\n            0\n",
            "class pattern is only allowed for eel classes",
        ),
    ]
    for source, message in cases:
        with pytest.raises(EelError, match=message):
            compile_source(source, "src/m.eel", "m")


def test_semantics_in_erl():
    tmp = pathlib.Path(tempfile.mkdtemp())
    (tmp / "src").mkdir()
    (tmp / "src" / "msg.eel").write_text(
        "def selective():\n"
        "    match receive():\n"
        "        case Ok(v):\n"
        "            v\n"
        "\n"
        "def guarded():\n"
        "    match receive():\n"
        "        case Error(e) if e > 0:\n"
        "            e\n"
        "        case _:\n"
        "            0\n"
        "\n"
        "def timed():\n"
        "    match receive():\n"
        "        case Ok(v):\n"
        "            v\n"
        "        case Timeout(50):\n"
        "            'timeout'\n"
        "\n"
        "def plain():\n"
        "    receive()\n"
        "\n"
        "def ping():\n"
        "    parent = self()\n"
        "    pid = spawn(lambda: send(parent, 'hi'))\n"
        "    match receive():\n"
        "        case msg:\n"
        "            (msg, pid)\n"
        "\n"
        "def child():\n"
        "    spawn(lambda: 1)\n"
    )
    subprocess.run(
        [sys.executable, "-m", "eel", "build", str(tmp), "--no-daemon"],
        cwd=ROOT, check=True, capture_output=True,
    )
    ebin = tmp / "build" / "dev" / "erlang" / tmp.name / "ebin"
    script = """
        Self = self(),
        S = spawn(fun() -> Self ! {s, msg:selective()} end),
        S ! {error, 1},          % matches no pattern — stays in the mailbox
        S ! {ok, 7},
        Sel = receive {s, V} -> V end,
        G = spawn(fun() -> Self ! {g, msg:guarded()} end),
        G ! {error, 5},
        Grd = receive {g, V2} -> V2 end,
        T = spawn(fun() -> Self ! {t, msg:timed()} end),
        Tim = receive {t, V3} -> V3 end,
        P = spawn(fun() -> Self ! {p, msg:plain()} end),
        P ! first, P ! second,
        Pl = receive {p, V4} -> V4 end,
        {Msg, Pid} = msg:ping(),
        Child = msg:child(),
        io:format("~p ~p ~p ~p ~p ~p", [Sel, Grd, Tim, Pl, Msg, is_pid(Pid) andalso is_pid(Child)]),
        halt().
    """
    out = subprocess.run(
        [ERL, "-noshell", "-pa", str(ebin), "-eval", script],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "7 5 timeout first hi true"
