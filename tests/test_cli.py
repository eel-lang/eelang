"""End-to-end skeleton: the build CLI compiles .eel into .beam, hello:hello() → <<"hi">>."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile

from platform_support import cli_build

ERL = shutil.which("erl")


def test_build_and_run():
    tmp = pathlib.Path(tempfile.mkdtemp())
    src = tmp / "src"
    src.mkdir()
    (src / "hello.eel").write_text('def hello():\n    "hi"\n')

    cli_build(tmp)

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
    cli_build(tmp, "--erl")
    assert (ebin / "hello.erl").exists()
