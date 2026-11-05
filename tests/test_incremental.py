"""Incremental build: manifest, skipping unchanged modules, pruning, flags, --force."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from squeal import compiler
from squeal.compiler import build

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _project(tmp_path: pathlib.Path, modules: dict[str, str]) -> pathlib.Path:
    proj = tmp_path / "app"
    (proj / "src").mkdir(parents=True)
    for name, body in modules.items():
        (proj / "src" / f"{name}.ee").write_text(f"def {name}():\n    {body}\n")
    return proj


def _beams(out: pathlib.Path) -> dict[str, int]:
    return {p.name: p.stat().st_mtime_ns for p in (out / "ebin").glob("*.beam")}


def test_second_build_recompiles_nothing(tmp_path):
    proj = _project(tmp_path, {"a": "1", "b": "2"})
    out = build(proj)
    before = _beams(out)

    build(proj)
    assert _beams(out) == before            # ничего не пересобрано и не перезаписано


def test_only_changed_module_recompiles(tmp_path):
    proj = _project(tmp_path, {"a": "1", "b": "2"})
    out = build(proj)
    before = _beams(out)

    (proj / "src" / "a.ee").write_text("def a():\n    11\n")
    build(proj)
    after = _beams(out)
    assert after["a.beam"] != before["a.beam"]
    assert after["b.beam"] == before["b.beam"]   # соседний модуль не тронут


def test_deleted_module_is_pruned(tmp_path):
    proj = _project(tmp_path, {"a": "1", "b": "2"})
    out = build(proj)
    assert (out / "ebin" / "b.beam").exists()

    (proj / "src" / "b.ee").unlink()
    build(proj)
    assert not (out / "ebin" / "b.beam").exists()
    app_file = next((out / "ebin").glob("*.app")).read_text()
    assert "{modules,[a]}" in app_file


def test_force_rebuilds_everything(tmp_path):
    proj = _project(tmp_path, {"a": "1", "b": "2"})
    out = build(proj)
    before = _beams(out)

    build(proj, force=True)
    after = _beams(out)
    assert all(after[name] != before[name] for name in before)


def test_missing_beam_is_rebuilt(tmp_path):
    proj = _project(tmp_path, {"a": "1"})
    out = build(proj)
    (out / "ebin" / "a.beam").unlink()

    build(proj)
    assert (out / "ebin" / "a.beam").exists()


def test_erl_flag_adds_the_missing_artifact(tmp_path):
    proj = _project(tmp_path, {"a": "1"})
    out = build(proj)
    assert not (out / "ebin" / "a.erl").exists()
    before = _beams(out)

    build(proj, erl=True)
    assert (out / "ebin" / "a.erl").exists()
    assert _beams(out)["a.beam"] != before["a.beam"]   # модуль пересобран


def test_broken_manifest_rebuilds(tmp_path):
    proj = _project(tmp_path, {"a": "1"})
    out = build(proj)
    before = _beams(out)

    (out / "_squeal_artefacts" / "build.json").write_text("{ not json")
    build(proj)
    assert _beams(out)["a.beam"] != before["a.beam"]


def test_compiler_change_invalidates(tmp_path, monkeypatch):
    proj = _project(tmp_path, {"a": "1"})
    out = build(proj)
    before = _beams(out)

    monkeypatch.setattr(compiler, "_FINGERPRINT", "different")
    build(proj)
    assert _beams(out)["a.beam"] != before["a.beam"]


def test_cli_is_incremental_too(tmp_path):
    proj = _project(tmp_path, {"a": "1"})
    out = build(proj)
    before = _beams(out)

    def cli(*extra):
        return subprocess.run(
            [sys.executable, "-m", "squeal", "build", str(proj), *extra],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )

    cli()
    assert _beams(out) == before
    cli("--force")
    assert _beams(out)["a.beam"] != before["a.beam"]
