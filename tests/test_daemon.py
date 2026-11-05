"""Compiler daemon (Erlang side): autostart, reuse, stop, errors via the API path."""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from eel import client, etf
from eel.compiler import DAEMON, PROTOCOL, build, daemon_pid_file, daemon_socket
from eel.errors import EelError
from eel.term import Atom

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def clean_processes():
    client.stop()
    DAEMON.stop()
    yield
    client.stop()
    DAEMON.stop()


def _project(tmp_path: pathlib.Path) -> pathlib.Path:
    proj = tmp_path / "app"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "hello.eel").write_text('def hello():\n    "hi"\n')
    return proj


def _pid() -> str:
    return daemon_pid_file().read_text().strip()


def test_build_uses_the_running_daemon(clean_processes, tmp_path):
    proj = _project(tmp_path)
    DAEMON.start()                             # так делает сервер сборки при старте
    build(proj)
    pid = _pid()
    info = DAEMON.ping()
    assert info["version"] == str(PROTOCOL)

    # правим исходник, чтобы вторая сборка действительно компилировала
    (proj / "src" / "hello.eel").write_text('def hello():\n    "hi again"\n')
    build(proj)
    assert _pid() == pid                       # тот же процесс Erlang
    assert int(DAEMON.ping()["compiled"]) == 2


def test_daemon_stop_and_restart(clean_processes, tmp_path):
    proj = _project(tmp_path)
    DAEMON.start()
    build(proj)
    assert daemon_socket().exists()

    assert DAEMON.stop() is True
    assert not daemon_socket().exists()
    assert DAEMON.ping() is None

    DAEMON.start()                             # новый демон поднимает сервер
    assert DAEMON.ping() is not None


def test_no_daemon_flag_keeps_everything_off(clean_processes, tmp_path):
    proj = _project(tmp_path)
    subprocess.run(
        [sys.executable, "-m", "eel", "build", str(proj), "--no-daemon"],
        cwd=ROOT, check=True, capture_output=True,
    )
    assert not daemon_socket().exists()
    assert DAEMON.ping() is None
    assert client.ping() is None               # сервер сборки тоже не поднимался


def test_compile_error_through_daemon(clean_processes, tmp_path):
    DAEMON.start()
    forms = [
        (Atom("attribute"), 0, Atom("module"), Atom("t")),
        (Atom("attribute"), 0, Atom("export"), [(Atom("m"), 0)]),
        (Atom("attribute"), 0, Atom("file"), ("src/t.eel", 0)),
        (Atom("function"), 2, Atom("m"), 0, [
            (Atom("clause"), 2, [], [],
             [(Atom("call"), 3, (Atom("atom"), 3, Atom("nope")), [])]),
        ]),
    ]
    blob = tmp_path / "t.etf"
    blob.write_bytes(etf.encode([(str(tmp_path / "t"), forms, "src/t.eel")]))

    with pytest.raises(EelError) as exc:
        DAEMON.compile(blob, False)
    assert str(exc.value) == "src/t.eel:3: function nope/0 is not defined"
    assert DAEMON.ping() is not None           # демон жив после ошибки
