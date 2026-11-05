"""Session fixture: leave the compiler daemon as we found it."""

from __future__ import annotations

import pytest

from squeal.compiler import DAEMON


@pytest.fixture(scope="session", autouse=True)
def _daemon_lifecycle():
    was_running = DAEMON.ping() is not None
    yield
    if not was_running:
        DAEMON.stop()
