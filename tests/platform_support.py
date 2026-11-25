"""What this platform lets the suite exercise.

The build server and the compiler daemon talk over unix sockets, and Windows has none.
There the suite drives the compiler the way that needs neither: `eel build --no-daemon`
and `build(daemon=False)` compile through an escript inside the process. Everywhere else
the tests go through the default path, as before.
"""

from __future__ import annotations

import pathlib
import socket
import subprocess
import sys

import pytest

#: Whether the platform has unix sockets at all.
UNIX_SOCKETS = hasattr(socket, "AF_UNIX")

#: The build server and the compiler daemon exist only on top of unix sockets.
needs_unix_sockets = pytest.mark.skipif(
    not UNIX_SOCKETS, reason="the build server and the compiler daemon need unix sockets"
)

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The flags that make `python -m eel build` compile on this platform.
BUILD_FLAGS = [] if UNIX_SOCKETS else ["--no-daemon"]


def cli_build(project, *extra, check: bool = True) -> subprocess.CompletedProcess:
    """`python -m eel build PROJECT` through the compiler this platform has."""
    return subprocess.run(
        [sys.executable, "-m", "eel", "build", str(project), *BUILD_FLAGS, *extra],
        cwd=ROOT, capture_output=True, text=True, check=check,
    )


def build(root, **options) -> pathlib.Path:
    """`eel.compiler.build` (the library entry point) through the compiler this platform has."""
    options.setdefault("daemon", UNIX_SOCKETS)
    from eel.compiler import build

    return build(root, **options)
