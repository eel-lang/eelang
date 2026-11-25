"""Session fixture: leave the background processes as we found them."""

from __future__ import annotations

import pytest

from eel import client
from eel.compiler import DAEMON

from platform_support import UNIX_SOCKETS


@pytest.fixture(scope="session", autouse=True)
def _daemons_lifecycle():
    if not UNIX_SOCKETS:
        # no unix sockets (Windows): there is no build server and no compiler daemon
        yield
        return
    server_was_running = client.ping() is not None
    compiler_was_running = DAEMON.ping() is not None
    yield
    if not server_was_running:
        client.stop()
    if not compiler_was_running:
        DAEMON.stop()
