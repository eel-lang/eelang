"""Build pipeline: ETF artefacts, optional .erl/.abstr, reused compiler process."""

from __future__ import annotations

import pathlib
import shutil
import subprocess

from squeal.compiler import COMPILER, build

ERL = shutil.which("erl")

SRC = 'def hello():\n    "hi"\n\ndef twice(xs):\n    [x * 2 for x in xs]\n'


def _project(tmp_path: pathlib.Path) -> pathlib.Path:
    proj = tmp_path / "app"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "hello.ee").write_text(SRC)
    return proj


def test_default_build_writes_beam_and_etf_only(tmp_path):
    proj = _project(tmp_path)
    out = build(proj)
    ebin, artefacts = out / "ebin", out / "_squeal_artefacts"

    assert (ebin / "hello.beam").exists()
    assert (ebin / "app.app").exists()
    assert not (ebin / "hello.erl").exists()          # .erl только по флагу
    assert (artefacts / "forms.etf").exists()          # формы — одним ETF-блобом
    assert not list(artefacts.glob("*.abstr"))         # читаемый текст — только по флагу
    assert not (artefacts / "_jobs.term").exists()


def test_erl_and_dump_forms_flags(tmp_path):
    proj = _project(tmp_path)
    build(proj)
    out = build(proj, erl=True, dump_forms=True)
    ebin, artefacts = out / "ebin", out / "_squeal_artefacts"

    assert (ebin / "hello.erl").exists()
    assert "hello" in (ebin / "hello.erl").read_text()
    assert (artefacts / "hello.abstr").read_text().startswith("{attribute,0,module,hello}")


def test_compiled_module_runs(tmp_path):
    proj = _project(tmp_path)
    out = build(proj)
    ebin = out / "ebin"
    result = subprocess.run(
        [ERL, "-noshell", "-pa", str(ebin), "-eval",
         'io:format("~p ~p~n", [hello:hello(), hello:twice([1, 2])]), halt().'],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == '<<"hi">> [2,4]'


def test_compiler_process_is_reused(tmp_path):
    proj = _project(tmp_path)
    build(proj)                       # прогреваем: процесс точно есть
    starts = COMPILER.starts
    build(proj)
    build(proj)
    assert COMPILER.starts == starts  # повторные сборки не поднимают Erlang-VM заново
