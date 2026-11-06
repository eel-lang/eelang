"""End-to-end skeleton: the build CLI compiles .eel into .beam, hello:hello() → <<"hi">>."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ERL = shutil.which("erl")
ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_build_and_run():
    tmp = pathlib.Path(tempfile.mkdtemp())
    src = tmp / "src"
    src.mkdir()
    (src / "hello.eel").write_text('def hello():\n    "hi"\n')

    subprocess.run(
        [sys.executable, "-m", "eel", "build", str(tmp)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )

    ebin = tmp / "build" / "dev" / "erlang" / tmp.name / "ebin"
    assert (ebin / "hello.beam").exists()
    # .erl only with --erl (by default the forms travel as an ETF blob)
    assert not (ebin / "hello.erl").exists()
    artefacts = tmp / "build" / "dev" / "erlang" / tmp.name / "_eel_artefacts"
    assert (artefacts / "forms.etf").exists()

    out = subprocess.run(
        [ERL, "-noshell", "-pa", str(ebin), "-eval",
         'io:format("~p~n",[hello:hello()]), halt().'],
        capture_output=True,
        text=True,
        check=True,
    )
    assert '<<"hi">>' in out.stdout

    # --erl writes the source next to the .beam
    subprocess.run(
        [sys.executable, "-m", "eel", "build", str(tmp), "--erl"],
        cwd=ROOT, check=True, capture_output=True,
    )
    assert (ebin / "hello.erl").exists()
