"""Background compiler daemon: autostart, reuse across processes, stop, fallback."""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from squeal import etf
from squeal.compiler import (
    DAEMON,
    PROTOCOL,
    build,
    daemon_pid_file,
    daemon_socket,
)
from squeal.errors import SquealError
from squeal.term import Atom

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def clean_daemon():
    DAEMON.stop()
    yield
    DAEMON.stop()


def _project(tmp_path: pathlib.Path) -> pathlib.Path:
    proj = tmp_path / "app"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "hello.ee").write_text('def hello():\n    "hi"\n')
    return proj


def _pid() -> str:
    return daemon_pid_file().read_text().strip()


def test_build_starts_daemon_and_reuses_it(clean_daemon, tmp_path):
    proj = _project(tmp_path)
    build(proj)
    pid = _pid()
    info = DAEMON.ping()
    assert info["version"] == str(PROTOCOL)

    build(proj)
    assert _pid() == pid                       # тот же процесс Erlang
    assert int(DAEMON.ping()["compiled"]) == 2


def test_daemon_survives_the_client_process(clean_daemon, tmp_path):
    proj = _project(tmp_path)

    def run_cli(*extra):
        return subprocess.run(
            [sys.executable, "-m", "squeal", "build", str(proj), *extra],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )

    run_cli()
    pid = _pid()
    run_cli()                                  # другая команда, другой процесс
    assert _pid() == pid
    assert int(DAEMON.ping()["compiled"]) == 2


def test_daemon_stop_and_restart(clean_daemon, tmp_path):
    proj = _project(tmp_path)
    build(proj)
    assert daemon_socket().exists()

    assert DAEMON.stop() is True
    assert not daemon_socket().exists()
    assert DAEMON.ping() is None

    build(proj)                                # новый демон поднимается сам
    assert DAEMON.ping() is not None


def test_cli_daemon_status(clean_daemon, tmp_path):
    def status():
        return subprocess.run(
            [sys.executable, "-m", "squeal", "daemon", "status"],
            cwd=ROOT, capture_output=True, text=True,
        )

    assert status().returncode == 1
    build(_project(tmp_path))
    result = status()
    assert result.returncode == 0
    assert "running" in result.stdout


def test_no_daemon_flag_keeps_it_off(clean_daemon, tmp_path):
    proj = _project(tmp_path)
    subprocess.run(
        [sys.executable, "-m", "squeal", "build", str(proj), "--no-daemon"],
        cwd=ROOT, check=True, capture_output=True,
    )
    assert not daemon_socket().exists()
    assert DAEMON.ping() is None


def test_compile_error_through_daemon(clean_daemon, tmp_path):
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
        DAEMON.compile(blob, False)
    assert str(exc.value) == "src/t.ee:3: function nope/0 is not defined"
    assert DAEMON.ping() is not None           # демон жив после ошибки
