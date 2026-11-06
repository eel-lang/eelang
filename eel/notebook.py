"""Eel's Jupyter integration, with no custom kernel.

It plugs in as an IPython extension (``c.InteractiveShellApp.extensions =
['eel.notebook']`` in ``ipython_config.py``). A cell stays Python text —
autocomplete and linting come from the Python LSP; execution is intercepted: Python runs
``pass``, while the source is compiled by eel and run in a persistent Erlang REPL.
"""

from __future__ import annotations

import ast
import sys

from .term import Atom


def find_comprehensions(node, out=None) -> list:
    """Find every comprehension form (`lc`/`mc`) in a cell's forms."""
    if out is None:
        out = []
    if isinstance(node, tuple):
        if node and isinstance(node[0], Atom) and node[0] in (Atom("lc"), Atom("mc")):
            if len(node) > 1 and node[1] != 0:      # annotation 0 means a compiler-internal form
                out.append(node)
        for item in node:
            find_comprehensions(item, out)
    elif isinstance(node, list):
        for item in node:
            find_comprehensions(item, out)
    return out


def compact(source: str) -> str:
    """erl_pp wraps lines — squeeze them into one for the console."""
    s = " ".join(source.split())
    for a, b in (("[ ", "["), (" ]", "]"), ("#{ ", "#{"), (" }", "}")):
        s = s.replace(a, b)
    return s


def load_ipython_extension(ipython) -> None:
    if getattr(ipython, "_eel_notebook", None) is not None:
        return  # already loaded
    from . import frontend
    from .errors import EelError
    from .repl import ErlangRepl
    from .session import Session

    state = {"session": Session(), "repl": None, "raw_cell": None}

    def let_cleanup(lines):
        """Rewrite `Ctor(args) = expr` before Python's parser (otherwise a SyntaxError)."""
        return frontend.rewrite_pattern_assignments("".join(lines)).splitlines(keepends=True)

    class _NoopTransformer(ast.NodeTransformer):
        """Replace the cell body with pass — Python executes nothing."""

        def visit_Module(self, node):  # noqa: N802
            node.body = [ast.Pass()]
            return node

    def pre_run_cell(info) -> None:
        state["raw_cell"] = info.raw_cell

    def post_run_cell(result) -> None:
        source = state["raw_cell"]
        if (
            source is None
            or result.error_before_exec is not None
            or result.error_in_exec is not None
        ):
            return  # Python has already shown the error (a SyntaxError, say)
        try:
            if state["repl"] is None:
                state["repl"] = ErlangRepl()
            forms, suppress = state["session"].compile(source)
            if not forms:
                return
            if not suppress:
                for comp in find_comprehensions(forms):
                    print(f"erl: {compact(state['repl'].pp(comp))}")
            status, value = state["repl"].eval_forms(forms)
            if status == "ok":
                if not suppress and value != "nil":
                    print(value)
            else:
                sys.stderr.write(f"eel runtime error: {value}\n")
        except SyntaxError:
            pass  # the cell is not valid Python syntax; that is visible in the output already
        except EelError as exc:
            sys.stderr.write(f"eel: {exc}\n")

    transformer = _NoopTransformer()
    ipython.events.register("pre_run_cell", pre_run_cell)
    ipython.events.register("post_run_cell", post_run_cell)
    ipython.ast_transformers.append(transformer)
    ipython.input_transformers_cleanup.append(let_cleanup)
    ipython._eel_notebook = {
        "state": state,
        "pre": pre_run_cell,
        "post": post_run_cell,
        "transformer": transformer,
        "cleanup": let_cleanup,
    }


def unload_ipython_extension(ipython) -> None:
    loaded = getattr(ipython, "_eel_notebook", None)
    if loaded is None:
        return
    for name, callback in (("pre_run_cell", loaded["pre"]), ("post_run_cell", loaded["post"])):
        try:
            ipython.events.callbacks[name].remove(callback)
        except (KeyError, ValueError):
            pass
    for seq, item in (
        (ipython.ast_transformers, loaded["transformer"]),
        (ipython.input_transformers_cleanup, loaded["cleanup"]),
    ):
        try:
            seq.remove(item)
        except ValueError:
            pass
    repl = loaded["state"].get("repl")
    if repl is not None:
        repl.close()
    ipython._eel_notebook = None
