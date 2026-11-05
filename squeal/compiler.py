"""Конвейер компиляции: source → IR → проверки → формы → .beam."""

from __future__ import annotations

import atexit
import base64
import pathlib
import subprocess

from . import backend, checks, etf, frontend
from .errors import SquealError
from .term import Atom, term_to_erlang

ESCRIPT = pathlib.Path(__file__).parent / "squeal_compile.escript"


def compile_source(source: str, path: str, name: str) -> list:
    """Исходник → Erlang-формы (значения Python)."""
    try:
        module = frontend.parse_module(source, path, name)
        checks.check(module)
        return backend.module_forms(module)
    except SquealError as e:
        msg = str(e)
        if not msg.startswith(f"{path}:"):
            raise SquealError(f"{path}:{msg}") from e
        raise


def module_name(relative_path: pathlib.PurePath) -> str:
    """``src/a/b/c.ee`` → ``a@b@c``; ``src/hello.ee`` → ``hello``."""
    return "@".join(relative_path.with_suffix("").parts)


class CompilerProcess:
    """Долгоживущий escript: один Erlang-VM на все сборки процесса.

    Одноразовый `python -m squeal build` выигрыша не получает — он и так поднимает VM один раз;
    переиспользование окупается при повторных сборках из одного процесса (API, тесты).
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self.starts = 0

    def _ensure(self) -> subprocess.Popen:
        if self._proc is None or self._proc.poll() is not None:
            self._proc = subprocess.Popen(
                ["escript", str(ESCRIPT)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            self.starts += 1
        return self._proc

    def compile(self, forms_path: pathlib.Path, erl: bool) -> None:
        """Скомпилировать ETF-блоб с формами; ошибки — SquealError с текстом от escript."""
        proc = self._ensure()
        request = term_to_erlang((str(forms_path), Atom("true") if erl else Atom("false")))
        try:
            proc.stdin.write(request + ".\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
        except (BrokenPipeError, OSError) as exc:
            raise SquealError("compiler process died") from exc
        if not line:
            stderr = proc.stderr.read().strip() if proc.stderr else ""
            raise SquealError(stderr or "compiler process exited")
        reply = line.rstrip("\n")
        if reply == "OK":
            return
        if reply.startswith("ERR "):
            raise SquealError(base64.b64decode(reply[4:]).decode("utf-8").strip())
        raise SquealError(f"unexpected compiler reply: {reply!r}")

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None


COMPILER = CompilerProcess()
atexit.register(COMPILER.close)


def build(
    root: str | pathlib.Path,
    *,
    erl: bool = False,
    dump_forms: bool = False,
) -> pathlib.Path:
    """Компилирует ``src/*.ee`` в ``build/dev/erlang/<app>/ebin``.

    ``erl`` — дополнительно писать ``.erl`` рядом с ``.beam``; ``dump_forms`` — писать читаемые
    ``.abstr`` (по умолчанию формы идут только ETF-блобом).
    """
    root = pathlib.Path(root).resolve()
    src = root / "src"
    app = root.name
    out = root / "build" / "dev" / "erlang" / app
    artefacts = out / "_squeal_artefacts"
    ebin = out / "ebin"
    artefacts.mkdir(parents=True, exist_ok=True)
    ebin.mkdir(parents=True, exist_ok=True)

    modules = []
    jobs = []
    for path in sorted(src.glob("**/*.ee")):
        rel = path.relative_to(src)
        name = module_name(rel)
        modules.append(name)
        source_path = path.relative_to(root)
        forms = compile_source(path.read_text(), str(source_path), name)
        jobs.append((name, forms, str(source_path)))
        if dump_forms:
            (artefacts / f"{name}.abstr").write_text(
                "".join(term_to_erlang(form) + ".\n" for form in forms)
            )

    if jobs:
        forms_path = artefacts / "forms.etf"
        forms_path.write_bytes(
            etf.encode([(str(ebin / name), forms, source) for name, forms, source in jobs])
        )
        COMPILER.compile(forms_path, erl)

    app_spec = (
        Atom("application"),
        Atom(app),
        [
            (Atom("vsn"), "0.1.0"),
            (Atom("modules"), [Atom(m) for m in modules]),
            (Atom("registered"), []),
            (Atom("applications"), [Atom("kernel"), Atom("stdlib")]),
        ],
    )
    (ebin / f"{app}.app").write_text(term_to_erlang(app_spec) + ".\n")
    return out
