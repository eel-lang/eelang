"""Session fixture: leave the background processes as we found them."""

from __future__ import annotations

import pytest

from eel import client
from eel.compiler import DAEMON


@pytest.fixture(scope="session", autouse=True)
def _daemons_lifecycle():
    server_was_running = client.ping() is not None
    compiler_was_running = DAEMON.ping() is not None
    yield
    if not server_was_running:
        client.stop()
    if not compiler_was_running:
        DAEMON.stop()
