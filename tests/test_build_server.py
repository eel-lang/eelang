"""Build server: live Python process serving builds over a unix socket (no watcher)."""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from eel import client
from eel.compiler import DAEMON
from eel.errors import EelError

ROOT = pathlib.Path(__file__).resolve().parent.parent

OK_SRC = 'def hello():\n    "hi"\n'


@pytest.fixture
def clean_servers():
    client.stop()
    DAEMON.stop()
    yield
    client.stop()
    DAEMON.stop()


def _project(tmp_path: pathlib.Path, source: str = OK_SRC) -> pathlib.Path:
    proj = tmp_path / "app"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "hello.eel").write_text(source)
    return proj


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "eel", *args], cwd=ROOT, capture_output=True, text=True
    )


def _beams(out: pathlib.Path) -> dict[str, int]:
    return {p.name: p.stat().st_mtime_ns for p in (out / "ebin").glob("*.beam")}


def test_first_build_starts_server_and_next_reuses_it(clean_servers, tmp_path):
    proj = _project(tmp_path)
    assert _cli("build", str(proj)).returncode == 0
    pid = client.server_pid_file().read_text().strip()

    (proj / "src" / "hello.eel").write_text('def hello():\n    "hi again"\n')
    assert _cli("build", str(proj)).returncode == 0
    assert client.server_pid_file().read_text().strip() == pid   # сервер тот же
    assert client.ping()["builds"] == 2


def test_build_remote_is_served_by_the_live_process(clean_servers, tmp_path):
    proj = _project(tmp_path)
    assert client.build_remote(proj) is True
    out = proj / "build" / "dev" / "erlang" / proj.name
    assert (out / "ebin" / "hello.beam").exists()
    before = _beams(out)

    assert client.build_remote(proj) is True          # ничего не менялось
    assert _beams(out) == before                      # и ничего не пересобрано

    (proj / "src" / "hello.eel").write_text('def hello():\n    "ho"\n')
    assert client.build_remote(proj) is True
    assert _beams(out)["hello.beam"] != before["hello.beam"]


def test_compile_error_is_reported_and_server_survives(clean_servers, tmp_path):
    proj = _project(tmp_path, 'def hello():\n    for x in [1]:\n        x\n')
    with pytest.raises(EelError, match="loop For is forbidden"):
        client.build_remote(proj)

    info = client.ping()
    assert info is not None
    assert info["errors"] >= 1

    (proj / "src" / "hello.eel").write_text(OK_SRC)     # сервер продолжает работать
    assert client.build_remote(proj) is True
    assert (proj / "build" / "dev" / "erlang" / proj.name / "ebin" / "hello.beam").exists()


def test_cli_reports_compile_errors(clean_servers, tmp_path):
    proj = _project(tmp_path, 'def hello():\n    for x in [1]:\n        x\n')
    result = _cli("build", str(proj))
    assert result.returncode == 1
    assert "eel: " in result.stderr and "loop For is forbidden" in result.stderr


def test_daemon_status_shows_both_processes(clean_servers, tmp_path):
    assert _cli("daemon", "status").returncode == 1

    _cli("build", str(_project(tmp_path)))
    result = _cli("daemon", "status")
    assert result.returncode == 0
    assert "build server: running" in result.stdout
    assert "compiler daemon: running" in result.stdout


def test_daemon_stop_stops_both(clean_servers, tmp_path):
    _cli("build", str(_project(tmp_path)))
    assert client.ping() is not None
    assert DAEMON.ping() is not None

    assert _cli("daemon", "stop").returncode == 0
    assert client.ping() is None
    assert DAEMON.ping() is None


def test_no_daemon_builds_in_process(clean_servers, tmp_path):
    proj = _project(tmp_path)
    assert _cli("build", str(proj), "--no-daemon").returncode == 0
    assert client.ping() is None                      # сервер не поднимался
    assert DAEMON.ping() is None                      # и демон компилятора тоже
    assert (proj / "build" / "dev" / "erlang" / proj.name / "ebin" / "hello.beam").exists()


def test_ping_reports_protocol(clean_servers, tmp_path):
    client.build_remote(_project(tmp_path))
    info = client.ping()
    assert info["version"] == client.SERVER_PROTOCOL


def test_compiler_daemon_is_born_with_the_server(clean_servers, tmp_path):
    assert DAEMON.ping() is None
    client.ensure()
    assert DAEMON.ping() is not None           # без единой сборки


def test_compiler_daemon_dies_with_the_server(clean_servers, tmp_path):
    client.ensure()
    assert DAEMON.ping() is not None
    client.stop()
    assert DAEMON.ping() is None


def test_compiler_daemon_dies_when_the_server_is_killed(clean_servers, tmp_path):
    import os
    import signal
    import time

    client.ensure()
    assert DAEMON.ping() is not None
    pid = int(client.server_pid_file().read_text())
    os.kill(pid, signal.SIGKILL)               # сервер умирает без шанса прибраться

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and DAEMON.ping() is not None:
        time.sleep(0.05)
    assert DAEMON.ping() is None               # escript ушёл по EOF на stdin


def test_library_build_does_not_start_the_daemon(clean_servers, tmp_path):
    from eel.compiler import build

    proj = _project(tmp_path)
    build(proj)                                # API без сервера: escript внутри процесса
    assert DAEMON.ping() is None               # демон рождается только вместе с сервером
    assert (proj / "build" / "dev" / "erlang" / proj.name / "ebin" / "hello.beam").exists()
