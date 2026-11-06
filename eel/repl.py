"""A persistent Erlang REPL: an escript subprocess exchanging abstract forms."""

from __future__ import annotations

import base64
import pathlib
import subprocess
import sys

from .term import Atom, term_to_erlang

ESCRIPT = pathlib.Path(__file__).parent / "repl.escript"


class ErlangRepl:
    def __init__(self):
        self.proc = subprocess.Popen(
            ["escript", str(ESCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )

    def _send(self, term) -> str:
        self.proc.stdin.write(term_to_erlang(term) + ".\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("repl process exited")
            if line.startswith("OUT "):
                # whatever the cell printed goes to stdout before the value
                sys.stdout.write(base64.b64decode(line[4:]).decode("utf-8"))
                sys.stdout.flush()
                continue
            return line.rstrip("\n")

    def eval_forms(self, forms: list) -> tuple[str, str]:
        """Send a list of forms and return ("ok" | "error", the value or error text).

        What `print` printed is written to stdout before the value is returned.
        """
        line = self._send(forms)
        if line.startswith("OK "):
            return "ok", line[3:]
        if line.startswith("ERR "):
            return "error", line[4:]
        raise RuntimeError(f"unexpected repl reply: {line!r}")

    def pp(self, form) -> str:
        """The Erlang source of a form (through erl_pp), for printing the equivalent."""
        line = self._send((Atom("$pp"), form))
        if not line.startswith("PP "):
            raise RuntimeError(f"unexpected repl reply: {line!r}")
        return base64.b64decode(line[3:]).decode("utf-8")

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.wait(timeout=5)
