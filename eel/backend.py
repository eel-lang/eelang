"""Backend: IR → the Erlang Abstract Format (as Python values).

The forms repeat Gleam 1.19 for matching constructs (see specs/architecture.md);
the Python-semantics operators (``// % **``) and destructuring are our own forms.
Every form is annotated with the ``.eel`` source line.
"""

from __future__ import annotations

from . import ir
from .errors import EelError
from .term import Atom

# Built-in functions: available until the user defines the name themselves.
BUILTINS = frozenset({"print", "zip", "enumerate", "range", "send", "self", "spawn"})

RESERVED_NAMES = frozenset({
    "eel_iter", "eel_mod", "eel_floordiv", "eel_on_load",
    "eel_print", "eel_str", "eel_str_tuple", "eel_str_map", "eel_join",
    "eel_zip", "eel_enumerate", "eel_range",
    "_eel_pow", "_eel_pow_loop",
})

COMPILE_OPTS = [
    Atom("no_auto_import"),
    Atom("nowarn_ignored"),
    Atom("nowarn_unused_vars"),
    Atom("nowarn_unused_function"),
    Atom("nowarn_nomatch"),
    Atom("inline"),
]

_OP_SYMBOL = {
    "Add": "+",
    "Sub": "-",
    "Mult": "*",
    "Div": "/",
    "Eq": "==",
    "NotEq": "/=",
    "Lt": "<",
    "LtE": "=<",
    "Gt": ">",
    "GtE": ">=",
}


def variable_name(name: str) -> Atom:
    """``x`` → ``X``, ``my_list`` → ``My_list`` (as in Gleam)."""
    return Atom(name[0].upper() + name[1:])


def _atom(line, name) -> tuple:
    return (Atom("atom"), line, Atom(name))


def _var(line, name) -> tuple:
    return (Atom("var"), line, Atom(name))


def _int(line, value) -> tuple:
    return (Atom("integer"), line, value)


def _local_call(line, name, args) -> tuple:
    return (Atom("call"), line, (Atom("atom"), line, Atom(name)), args)


def _remote_call(line, module, function, args) -> tuple:
    return (
        Atom("call"),
        line,
        (Atom("remote"), line, (Atom("atom"), line, Atom(module)), (Atom("atom"), line, Atom(function))),
        args,
    )


def _op(line, op, left, right) -> tuple:
    return (Atom("op"), line, Atom(op), left, right)


def _pattern_names(pat) -> list:
    """The names a pattern binds (so an assignment can shadow definitions)."""
    if isinstance(pat, (ir.PName, ir.PStar)):
        return [pat.name]
    if isinstance(pat, (ir.PList, ir.PTuple)):
        names = []
        for item in pat.items:
            names.extend(_pattern_names(item))
        return names
    return []



# --- standard-library methods ---


def _list_expr(line: int, forms: list) -> tuple:
    """A list literal as an expression: a cons chain."""
    out = (Atom("nil"), line)
    for form in reversed(forms):
        out = (Atom("cons"), line, form, out)
    return out


def _bin_literal(line: int, text: str) -> tuple:
    return (
        Atom("bin"),
        line,
        [(Atom("bin_element"), line, (Atom("string"), line, text), Atom("default"),
          [Atom("utf8")])],
    )


def _m_startswith(line, v, args):
    return _op(line, "=/=", _remote_call(line, "string", "prefix", [v, args[0]]),
               (Atom("atom"), line, Atom("nomatch")))


def _m_endswith(line, v, args):
    # OTP has no string:suffix/2: compare the tail exactly with binary:part/3
    def size(form):
        return _remote_call(line, "erlang", "byte_size", [form])

    tail = _remote_call(
        line, "binary", "part",
        [v, _op(line, "-", size(v), size(args[0])), size(args[0])],
    )
    return _op(line, "andalso", _op(line, ">=", size(v), size(args[0])),
               _op(line, "=:=", tail, args[0]))


def _m_lower(line, v, args):
    return _remote_call(line, "string", "lowercase", [v])


def _m_upper(line, v, args):
    return _remote_call(line, "string", "uppercase", [v])


def _m_strip(line, v, args):
    return _remote_call(line, "string", "trim", [v])


def _m_lstrip(line, v, args):
    return _remote_call(line, "string", "trim", [v, (Atom("atom"), line, Atom("leading"))])


def _m_rstrip(line, v, args):
    return _remote_call(line, "string", "trim", [v, (Atom("atom"), line, Atom("trailing"))])


def _m_split0(line, v, args):
    return _remote_call(line, "string", "lexemes", [v, (Atom("string"), line, " \t\n\r\f\v")])


def _m_split1(line, v, args):
    return _remote_call(line, "string", "split", [v, args[0], (Atom("atom"), line, Atom("all"))])


def _m_replace(line, v, args):
    return _remote_call(line, "binary", "replace",
                        [v, args[0], args[1], _list_expr(line, [(Atom("atom"), line, Atom("global"))])])


_STR_METHODS = {
    ("startswith", 1): _m_startswith,
    ("endswith", 1): _m_endswith,
    ("lower", 0): _m_lower,
    ("upper", 0): _m_upper,
    ("strip", 0): _m_strip,
    ("lstrip", 0): _m_lstrip,
    ("rstrip", 0): _m_rstrip,
    ("split", 0): _m_split0,
    ("split", 1): _m_split1,
    ("replace", 2): _m_replace,
}


class _Gen:
    def __init__(self):
        self._temp = 0
        self._used_pow = False
        self._used_iter = False
        self._used_mod = False
        self._used_floordiv = False
        self._used_print = False
        self._used_join = False
        self._used_zip = False
        self._used_enumerate = False
        self._used_range = False
        self._shell_mode = False
        self._imports: dict[str, tuple] = {}
        self._scope: dict[str, Atom] = {}
        self._taken: dict[str, int] = {}
        self._defs: dict[tuple, Atom] = {}
        self._later_defs: set[tuple] = set()
        self._module_defs: set[tuple] = set()
        self._module_name = "m"
        self._decorated: list = []

    def _helper_call(self, line, name, args) -> tuple:
        """A helper call: a local function in a module, a bound fun in the REPL."""
        if self._shell_mode:
            return (Atom("call"), line, _var(line, name), args)
        return _local_call(line, name, args)

    # --- variable names ---

    def _allocate(self, name: str) -> Atom:
        """A fresh Erlang name for a binding.

        The counter is kept per base Erlang name: `N` and `n` both map to `N`, so the second
        one becomes `N@1` (otherwise two different Python names would collide).
        """
        base = variable_name(name)
        count = self._taken.get(base, 0)
        new = base if count == 0 else Atom(f"{base}@{count}")
        self._taken[base] = count + 1
        return new

    def _drop_defs(self, name: str) -> None:
        for key in [k for k in self._defs if k[0] == name]:
            del self._defs[key]

    def _bind(self, name: str) -> Atom:
        new = self._allocate(name)
        self._scope[name] = new
        return new

    def _bind_def(self, name: str, arity: int) -> Atom:
        """A function definition: the name is distinguished by arity (as in Erlang/Gleam)."""
        new = self._allocate(name)
        self._defs[(name, arity)] = new
        self._scope[name] = new  # a function is a value too (the latest arity)
        return new

    def _resolve(self, name: str, line: int) -> Atom:
        erlang = self._scope.get(name)
        if erlang is None:
            raise EelError(f"{line}: name {name!r} is not defined")
        return erlang

    # --- module and functions ---

    def module_forms(self, module: ir.Module) -> list:
        self._imports = dict(module.imports)
        self._module_defs = {(f.name, len(f.params)) for f in module.functions}
        self._module_name = module.name
        self._decorated = []
        func_forms = []
        for function in module.functions:
            func_forms.extend(self.function_forms(function))
        forms = [(Atom("attribute"), 0, Atom("module"), Atom(module.name))]
        if self._decorated:
            forms.append(
                (Atom("attribute"), 0, Atom("on_load"), (Atom("eel_on_load"), 0))
            )
        forms.extend([
            (Atom("attribute"), 0, Atom("compile"), list(COMPILE_OPTS)),
            (
                Atom("attribute"),
                0,
                Atom("export"),
                [(Atom(f.name), len(f.params)) for f in module.functions],
            ),
            (Atom("attribute"), 0, Atom("export_type"), []),
            (Atom("attribute"), 0, Atom("file"), (module.path, 0)),
        ])
        forms.extend(func_forms)
        if self._decorated:
            forms.append(self.on_load_form())
        if self._used_pow:
            forms.extend(self.pow_helpers())
        if self._used_iter:
            forms.append(self.iter_helper())
        if self._used_mod:
            forms.append(self.mod_helper())
        if self._used_floordiv:
            forms.append(self.floordiv_helper())
        if self._used_print:
            forms.extend(self.print_helpers())
        if self._used_join:
            forms.append(self.join_helper())
        if self._used_zip:
            forms.append(self.zip_helper())
        if self._used_enumerate:
            forms.append(self.enumerate_helper())
        if self._used_range:
            forms.append(self.range_helper())
        return forms

    def function_forms(self, function: ir.Function) -> list:
        if not function.decorators:
            return [self._function_form(function, Atom(function.name))]
        impl = Atom(f"{function.name}@impl")
        impl_form = self._function_form(function, impl)
        self._decorated.append((function, impl))
        return [impl_form, self._decorated_module_form(function)]

    def _function_form(self, function: ir.Function, name: Atom) -> tuple:
        self._scope = {}
        self._taken = {}
        self._defs = {}
        self._temp = 0
        line = function.line

        param_forms = []
        for p in function.params:
            param_forms.append(_var(line, self._bind(p)))

        body = [self.stmt_form(s) for s in function.statements[:-1]]
        body.append(self.tail_stmt_form(function.statements[-1]))
        return (
            Atom("function"),
            line,
            name,
            len(param_forms),
            [(Atom("clause"), line, param_forms, [], body)],
        )

    @staticmethod
    def _key_form(line: int, head: str, name: str, arity: int) -> tuple:
        """The tuple literal `{head, name, arity}` as an expression form."""
        return (Atom("tuple"), line, [
            (Atom("atom"), line, Atom(head)),
            (Atom("atom"), line, Atom(name)),
            (Atom("integer"), line, arity),
        ])

    def _decorator_key(self, line: int, name: str, arity: int) -> tuple:
        return self._key_form(line, self._module_name, name, arity)

    def _persistent_get(self, line: int, key: tuple) -> tuple:
        return _remote_call(line, "persistent_term", "get", [key])

    def _decorated_module_form(self, function: ir.Function) -> tuple:
        """The wrapper: `f(A, B) -> (persistent_term:get({Mod, f, N}))(A, B).`"""
        line = function.line
        arity = len(function.params)
        params = [_var(line, f"P{i}") for i in range(arity)]
        callee = self._persistent_get(
            line, self._decorator_key(line, function.name, arity)
        )
        body = (Atom("call"), line, callee, list(params))
        return (
            Atom("function"),
            line,
            Atom(function.name),
            arity,
            [(Atom("clause"), line, list(params), [], [body])],
        )

    def on_load_form(self) -> tuple:
        """`eel_on_load/0`: applies the decorators once, when the module loads."""
        line = 0
        puts = []
        for function, impl in self._decorated:
            arity = len(function.params)
            decorated = (Atom("fun"), line, (Atom("function"), impl, arity))
            for dec in reversed(function.decorators):
                decorated = (Atom("call"), line, self._decorator_value(dec, shell=False), [decorated])
            puts.append(
                _remote_call(
                    line,
                    "persistent_term",
                    "put",
                    [self._decorator_key(line, function.name, arity), decorated],
                )
            )
        body = puts + [(Atom("atom"), line, Atom("ok"))]
        return (
            Atom("function"),
            line,
            Atom("eel_on_load"),
            0,
            [(Atom("clause"), line, [], [], body)],
        )

    def _decorator_value(self, dec, shell: bool) -> tuple:
        """The form of a decorator value (a fun of arity 1)."""
        line = dec.line
        if isinstance(dec, ir.FunRef):
            # a name in scope (a nested function/variable) — call it through the variable;
            # an imported name — a remote fun; otherwise it is a function of the module
            if shell or dec.name in self._scope:
                return _var(line, self._resolve(dec.name, line))
            if dec.name in self._imports:
                module, fname = self._imports[dec.name]
                return self._remote_fun(line, module, fname)
            if (dec.name, 1) in self._module_defs or (dec.name, 1) in self._later_defs:
                return (Atom("fun"), line, (Atom("function"), Atom(dec.name), 1))
            other = [a for (n, a) in sorted(self._module_defs) if n == dec.name]
            if other:
                raise EelError(
                    f"{line}: decorator {dec.name} must take 1 argument "
                    f"(defined as {dec.name}/{other[0]})"
                )
            raise EelError(f"{line}: decorator {dec.name}/1 is not defined")
        if isinstance(dec, ir.RemoteFunRef):
            return self._remote_fun(line, dec.module, dec.name)
        if isinstance(dec, ir.Call):
            return self.expr_form(dec)
        if isinstance(dec, ir.RemoteCall):
            return _remote_call(
                line, dec.module, dec.name, [self.expr_form(a) for a in dec.args]
            )
        raise EelError(f"{line}: unsupported decorator: {dec!r}")

    @staticmethod
    def _remote_fun(line: int, module: str, name: str) -> tuple:
        """`fun mod:name/1` — the arity must be an {integer, Line, N} form."""
        return (
            Atom("fun"),
            line,
            (
                Atom("function"),
                (Atom("atom"), line, Atom(module)),
                (Atom("atom"), line, Atom(name)),
                (Atom("integer"), line, 1),
            ),
        )

    def decorated_def_forms(self, stmt: ir.Def) -> list:
        """Shell mode: a placeholder through the process dictionary plus the decorators."""
        line = stmt.line
        arity = len(stmt.params)
        name = self._bind_def(stmt.name, arity)
        key = self._key_form(line, "eel", stmt.name, arity)
        get_call = _remote_call(line, "erlang", "get", [key])
        params = [_var(line, f"P{i}") for i in range(arity)]
        placeholder_body = (Atom("call"), line, get_call, list(params))
        placeholder = (
            Atom("fun"),
            line,
            (Atom("clauses"), [(Atom("clause"), line, list(params), [], [placeholder_body])]),
        )
        saved_scope = dict(self._scope)
        saved_taken = dict(self._taken)
        saved_defs = dict(self._defs)
        param_forms = [_var(line, self._bind(p)) for p in stmt.params]
        body = [self.stmt_form(s) for s in stmt.statements[:-1]]
        body.append(self.tail_stmt_form(stmt.statements[-1]))
        self._scope = saved_scope
        self._taken = saved_taken
        self._defs = saved_defs
        raw = (
            Atom("fun"),
            line,
            (Atom("clauses"), [(Atom("clause"), line, param_forms, [], body)]),
        )
        decorated = raw
        for dec in reversed(stmt.decorators):
            decorated = (Atom("call"), line, self._decorator_value(dec, shell=True), [decorated])
        put = _remote_call(line, "erlang", "put", [key, decorated])
        return [(Atom("match"), line, _var(line, name), placeholder), put]

    # --- statements ---

    def tail_stmt_form(self, stmt) -> tuple:
        """The last statement of a body: `if`/`match` may yield a value without a trailing
        assignment. The old behaviour (all branches assign one variable) is kept —
        the variable stays bound."""
        if isinstance(stmt, (ir.If, ir.Match)):
            return self._case_tail(stmt)
        return self.stmt_form(stmt)

    def _tail_of(self, stmt):
        """The name a branch binds in its tail, or None."""
        if isinstance(stmt, ir.Assign) and isinstance(stmt.target, ir.PName):
            return stmt.target.name
        if isinstance(stmt, ir.NestedDef):
            return stmt.name  # a nested def binds a name too
        if isinstance(stmt, ir.If):
            return self._tail_assign_target([stmt.then_body, stmt.else_body])
        if isinstance(stmt, ir.Match):
            return self._tail_assign_target([c.body for c in stmt.cases])
        return None

    def _tail_assign_target(self, bodies):
        names = []
        for stmts in bodies:
            if not stmts:
                return None
            name = self._tail_of(stmts[-1])
            if name is None:
                return None
            names.append(name)
        return names[0] if len(set(names)) == 1 else None

    def _case_tail(self, node) -> tuple:
        if self._tail_assign_target(
            [node.then_body, node.else_body]
            if isinstance(node, ir.If)
            else [c.body for c in node.cases]
            + ([node.after_body] if getattr(node, "after_body", ()) else [])
        ) is not None:
            target, case = self._case_value(node)
            return (Atom("match"), node.line, _var(node.line, self._bind(target)), case)
        return self._case_value_only(node)

    def _branch_tail(self, stmts, line):
        saved_scope = dict(self._scope)
        saved_taken = dict(self._taken)
        saved_defs = dict(self._defs)
        body = self._branch_tail_body(stmts, line)
        self._scope = saved_scope
        self._taken = saved_taken
        self._defs = saved_defs
        return body

    def _branch_tail_body(self, stmts, line):
        if not stmts:
            raise EelError(f"{line}: branch has no value (empty block)")
        body = [self.stmt_form(s) for s in stmts[:-1]]
        last = stmts[-1]
        if isinstance(last, (ir.If, ir.Match)):
            body.append(self._case_value_only(last))
        elif isinstance(last, (ir.Assign, ir.NestedDef)):
            body.append(self.stmt_form(last))
        elif isinstance(last, ir.Expr):
            body.append(self.expr_form(last.value))
        else:
            raise EelError(f"{last.line}: branch tail must be an expression")
        return body

    def _case_value_only(self, node) -> tuple:
        if isinstance(node, ir.If):
            cond = self.expr_form(node.condition)
            then_body = self._branch_tail(node.then_body, node.line)
            else_body = self._branch_tail(node.else_body, node.line)
            return (Atom("case"), node.line, cond, [
                (Atom("clause"), node.line, [_atom(node.line, "true")], [], then_body),
                (Atom("clause"), node.line, [_atom(node.line, "false")], [], else_body),
            ])
        if isinstance(node.subject, ir.Receive):
            form, _targets = self._receive_build(node, tail=True)
            return form
        subject = self.expr_form(node.subject)
        clauses = []
        for c in node.cases:
            saved_scope = dict(self._scope)
            saved_taken = dict(self._taken)
            saved_defs = dict(self._defs)
            patterns = self._case_patterns(c.pattern)
            guard = [[self.expr_form(c.guard)]] if c.guard is not None else []
            body = self._branch_tail_body(c.body, c.line)
            self._scope = saved_scope
            self._taken = saved_taken
            self._defs = saved_defs
            for p in patterns:
                clauses.append((Atom("clause"), c.line, [p], guard, body))
        return (Atom("case"), node.line, subject, clauses)

    # --- statements ---

    def stmt_form(self, stmt) -> tuple:
        if isinstance(stmt, ir.Assign):
            value = self.expr_form(stmt.value)
            for name in _pattern_names(stmt.target):
                self._drop_defs(name)  # an assignment shadows a function
            pattern = self._pattern(stmt.target)
            return (Atom("match"), stmt.line, pattern, value)
        if isinstance(stmt, ir.Expr):
            return self.expr_form(stmt.value)
        if isinstance(stmt, ir.Def):
            return self.def_form(stmt, self._bind_def(stmt.name, len(stmt.params)))
        if isinstance(stmt, ir.NestedDef):
            return self._nested_def_form(stmt)
        if isinstance(stmt, (ir.If, ir.Match)):
            target, case = self._case_value(stmt)
            return (Atom("match"), stmt.line, _var(stmt.line, self._bind(target)), case)
        raise EelError(f"cannot generate a statement for: {stmt!r}")

    def def_form(self, stmt: ir.Def, name: Atom) -> tuple:
        """`F = fun F(X) -> … end` — a named fun, so recursion works."""
        line = stmt.line
        self._later_defs.discard((stmt.name, len(stmt.params)))
        saved_scope = dict(self._scope)
        saved_taken = dict(self._taken)
        saved_defs = dict(self._defs)
        param_forms = [_var(line, self._bind(p)) for p in stmt.params]
        body = [self.stmt_form(s) for s in stmt.statements[:-1]]
        body.append(self.tail_stmt_form(stmt.statements[-1]))
        self._scope = saved_scope
        self._taken = saved_taken
        self._defs = saved_defs
        fun = (
            Atom("named_fun"),
            line,
            name,
            [(Atom("clause"), line, param_forms, [], body)],
        )
        return (Atom("match"), line, _var(line, name), fun)

    def _nested_def_form(self, stmt: ir.NestedDef) -> tuple:
        """A nested `def` — bind a name to a named_fun (recursion) plus decorators at once."""
        line = stmt.line
        if stmt.name in RESERVED_NAMES:
            raise EelError(f"{line}: name {stmt.name!r} is reserved by the compiler")
        self._drop_defs(stmt.name)  # a module/cell function name is shadowed by a variable
        name = self._bind(stmt.name)
        saved_scope = dict(self._scope)
        saved_taken = dict(self._taken)
        saved_defs = dict(self._defs)
        param_forms = [_var(line, self._bind(p)) for p in stmt.params]
        body = [self.stmt_form(s) for s in stmt.statements[:-1]]
        body.append(self.tail_stmt_form(stmt.statements[-1]))
        self._scope = saved_scope
        self._taken = saved_taken
        self._defs = saved_defs
        value = (
            Atom("named_fun"),
            line,
            name,
            [(Atom("clause"), line, param_forms, [], body)],
        )
        for dec in reversed(stmt.decorators):
            value = (Atom("call"), line,
                     self._decorator_value(dec, shell=self._shell_mode), [value])
        return (Atom("match"), line, _var(line, name), value)

    def _receive_build(self, node, tail: bool) -> tuple:
        """`match receive():` → an Erlang receive with clauses and, if present, an after branch.

        Returns (form, branch targets) — the targets feed the "branches assign one variable" rule.
        """
        clauses = []
        targets = []
        for c in node.cases:
            saved = (dict(self._scope), dict(self._taken), dict(self._defs))
            patterns = self._case_patterns(c.pattern)
            guard = [[self.expr_form(c.guard)]] if c.guard is not None else []
            if tail:
                body = self._branch_tail_body(c.body, c.line)
            else:
                c_target, branch_body, result = self._branch_body(c.body, c.line)
                targets.append(c_target)
                body = branch_body + [result]
            self._scope, self._taken, self._defs = saved
            for p in patterns:
                clauses.append((Atom("clause"), c.line, [p], guard, body))

        if node.after_timeout is None:
            if not clauses:
                raise EelError(f"{node.line}: receive without case clauses")
            return (Atom("receive"), node.line, clauses), targets

        timeout = self.expr_form(node.after_timeout)
        saved = (dict(self._scope), dict(self._taken), dict(self._defs))
        if tail:
            after_body = self._branch_tail_body(node.after_body, node.line)
        else:
            a_target, a_body, a_result = self._branch_body(node.after_body, node.line)
            targets.append(a_target)
            after_body = a_body + [a_result]
        self._scope, self._taken, self._defs = saved
        return (Atom("receive"), node.line, clauses, timeout, after_body), targets

    def _case_value(self, node):
        if isinstance(node, ir.Match) and isinstance(node.subject, ir.Receive):
            form, targets = self._receive_build(node, tail=False)
            self._check_target(node.line, targets)
            return targets[0], form
        if isinstance(node, ir.If):
            cond = self.expr_form(node.condition)
            then_body = self._branch_tail(node.then_body, node.line)
            else_body = self._branch_tail(node.else_body, node.line)
            return (Atom("case"), node.line, cond, [
                (Atom("clause"), node.line, [_atom(node.line, "true")], [], then_body),
                (Atom("clause"), node.line, [_atom(node.line, "false")], [], else_body),
            ])
        if isinstance(node.subject, ir.Receive):
            form, _targets = self._receive_build(node, tail=True)
            return form
        subject = self.expr_form(node.subject)
        clauses = []
        for c in node.cases:
            saved_scope = dict(self._scope)
            saved_taken = dict(self._taken)
            saved_defs = dict(self._defs)
            patterns = self._case_patterns(c.pattern)
            guard = [[self.expr_form(c.guard)]] if c.guard is not None else []
            body = self._branch_tail_body(c.body, c.line)
            self._scope = saved_scope
            self._taken = saved_taken
            self._defs = saved_defs
            for p in patterns:
                clauses.append((Atom("clause"), c.line, [p], guard, body))
        return (Atom("case"), node.line, subject, clauses)

    # --- statements ---

    def stmt_form(self, stmt) -> tuple:
        if isinstance(stmt, ir.Assign):
            value = self.expr_form(stmt.value)
            for name in _pattern_names(stmt.target):
                self._drop_defs(name)  # an assignment shadows a function
            pattern = self._pattern(stmt.target)
            return (Atom("match"), stmt.line, pattern, value)
        if isinstance(stmt, ir.Expr):
            return self.expr_form(stmt.value)
        if isinstance(stmt, ir.Def):
            return self.def_form(stmt, self._bind_def(stmt.name, len(stmt.params)))
        if isinstance(stmt, ir.NestedDef):
            return self._nested_def_form(stmt)
        if isinstance(stmt, (ir.If, ir.Match)):
            target, case = self._case_value(stmt)
            return (Atom("match"), stmt.line, _var(stmt.line, self._bind(target)), case)
        raise EelError(f"cannot generate a statement for: {stmt!r}")

    def def_form(self, stmt: ir.Def, name: Atom) -> tuple:
        """`F = fun F(X) -> … end` — a named fun, so recursion works."""
        line = stmt.line
        self._later_defs.discard((stmt.name, len(stmt.params)))
        saved_scope = dict(self._scope)
        saved_taken = dict(self._taken)
        saved_defs = dict(self._defs)
        param_forms = [_var(line, self._bind(p)) for p in stmt.params]
        body = [self.stmt_form(s) for s in stmt.statements[:-1]]
        body.append(self.tail_stmt_form(stmt.statements[-1]))
        self._scope = saved_scope
        self._taken = saved_taken
        self._defs = saved_defs
        fun = (
            Atom("named_fun"),
            line,
            name,
            [(Atom("clause"), line, param_forms, [], body)],
        )
        return (Atom("match"), line, _var(line, name), fun)

    def _nested_def_form(self, stmt: ir.NestedDef) -> tuple:
        """A nested `def` — bind a name to a named_fun (recursion) plus decorators at once."""
        line = stmt.line
        if stmt.name in RESERVED_NAMES:
            raise EelError(f"{line}: name {stmt.name!r} is reserved by the compiler")
        self._drop_defs(stmt.name)  # a module/cell function name is shadowed by a variable
        name = self._bind(stmt.name)
        saved_scope = dict(self._scope)
        saved_taken = dict(self._taken)
        saved_defs = dict(self._defs)
        param_forms = [_var(line, self._bind(p)) for p in stmt.params]
        body = [self.stmt_form(s) for s in stmt.statements[:-1]]
        body.append(self.tail_stmt_form(stmt.statements[-1]))
        self._scope = saved_scope
        self._taken = saved_taken
        self._defs = saved_defs
        value = (
            Atom("named_fun"),
            line,
            name,
            [(Atom("clause"), line, param_forms, [], body)],
        )
        for dec in reversed(stmt.decorators):
            value = (Atom("call"), line,
                     self._decorator_value(dec, shell=self._shell_mode), [value])
        return (Atom("match"), line, _var(line, name), value)

    def _receive_form(self, node, tail: bool) -> tuple:
        """`match receive():` → an Erlang receive with clauses and, if present, an after branch."""
        clauses = []
        for c in node.cases:
            saved_scope = dict(self._scope)
            saved_taken = dict(self._taken)
            saved_defs = dict(self._defs)
            patterns = self._case_patterns(c.pattern)
            guard = [[self.expr_form(c.guard)]] if c.guard is not None else []
            if tail:
                body = self._branch_tail_body(c.body, c.line)
            else:
                _, branch_body, result = self._branch_body(c.body, c.line)
                body = branch_body + [result]
            self._scope = saved_scope
            self._taken = saved_taken
            self._defs = saved_defs
            for p in patterns:
                clauses.append((Atom("clause"), c.line, [p], guard, body))
        if node.after_timeout is None:
            return (Atom("receive"), node.line, clauses)
        timeout = self.expr_form(node.after_timeout)
        saved_scope = dict(self._scope)
        saved_taken = dict(self._taken)
        saved_defs = dict(self._defs)
        if tail:
            after_body = self._branch_tail_body(node.after_body, node.line)
        else:
            _, branch_body, result = self._branch_body(node.after_body, node.line)
            after_body = branch_body + [result]
        self._scope = saved_scope
        self._taken = saved_taken
        self._defs = saved_defs
        return (Atom("receive"), node.line, clauses, timeout, after_body)

    def _case_value(self, node):
        if isinstance(node, ir.Match) and isinstance(node.subject, ir.Receive):
            form = self._receive_form(node, tail=False)
            target = self._check_target(node.line, [c.body for c in node.cases]
                                       + ([node.after_body] if node.after_body else []))
            return target, form
        if isinstance(node, ir.If):
            cond = self.expr_form(node.condition)
            t_target, t_body, t_result = self._branch(node.then_body, node.line)
            e_target, e_body, e_result = self._branch(node.else_body, node.line)
            self._check_target(node.line, [t_target, e_target])
            case = (Atom("case"), node.line, cond, [
                (Atom("clause"), node.line, [_atom(node.line, "true")], [], t_body + [t_result]),
                (Atom("clause"), node.line, [_atom(node.line, "false")], [], e_body + [e_result]),
            ])
            return t_target, case
        if isinstance(node, ir.Match):
            subject = self.expr_form(node.subject)
            clauses = []
            targets = []
            for c in node.cases:
                saved_scope = dict(self._scope)
                saved_taken = dict(self._taken)
                saved_defs = dict(self._defs)
                patterns = self._case_patterns(c.pattern)  # binds the pattern variables
                guard = [[self.expr_form(c.guard)]] if c.guard is not None else []
                c_target, c_body, c_result = self._branch_body(c.body, c.line)
                self._scope = saved_scope
                self._taken = saved_taken
                self._defs = saved_defs
                targets.append(c_target)
                for p in patterns:
                    clauses.append((Atom("clause"), c.line, [p], guard, c_body + [c_result]))
            self._check_target(node.line, targets)
            return targets[0], (Atom("case"), node.line, subject, clauses)
        raise EelError(f"not a branch: {node!r}")

    def _branch(self, stmts, line):
        saved_scope = dict(self._scope)
        saved_taken = dict(self._taken)
        saved_defs = dict(self._defs)
        target, body, result = self._branch_body(stmts, line)
        self._scope = saved_scope
        self._taken = saved_taken
        self._defs = saved_defs
        return target, body, result

    def _branch_body(self, stmts, line):
        if not stmts:
            raise EelError(f"{line}: branch has no value (empty block)")
        body = []
        for stmt in stmts[:-1]:
            body.append(self.stmt_form(stmt))
        last = stmts[-1]
        if isinstance(last, ir.Assign) and isinstance(last.target, ir.PName):
            target = last.target.name
            result = self.expr_form(last.value)
        elif isinstance(last, (ir.If, ir.Match)):
            target, result = self._case_value(last)
        elif isinstance(last, ir.NestedDef):
            target, result = last.name, self.stmt_form(last)
        else:
            raise EelError(
                f"{last.line}: branch must end with an assignment to the same variable"
            )
        return target, body, result

    def _check_target(self, line, targets):
        if len(set(targets)) != 1:
            raise EelError(f"{line}: branches assign different variables: {targets}")

    def _has_bindings(self, pat):
        if isinstance(pat, (ir.PName, ir.PStar)):
            return True
        if isinstance(pat, (ir.PList, ir.PTuple)):
            return any(self._has_bindings(i) for i in pat.items)
        if isinstance(pat, ir.PMap):
            return any(self._has_bindings(v) for _, v in pat.items)
        if isinstance(pat, ir.OrPattern):
            return any(self._has_bindings(p) for p in pat.patterns)
        return False

    def _case_patterns(self, pattern):
        if isinstance(pattern, ir.OrPattern):
            if any(self._has_bindings(p) for p in pattern.patterns):
                raise EelError(
                    f"{pattern.line}: or-pattern cannot bind variables"
                )
            out = []
            for p in pattern.patterns:
                out.extend(self._case_patterns(p))
            return out
        return [self._pattern(pattern)]

    def _pattern(self, pat) -> tuple:
        if isinstance(pat, ir.PName):
            return _var(pat.line, self._bind(pat.name))
        if isinstance(pat, ir.PTuple):
            return (Atom("tuple"), pat.line, [self._pattern(i) for i in pat.items])
        if isinstance(pat, ir.PList):
            return self._list_pattern(pat)
        if isinstance(pat, ir.Wildcard):
            return _var(pat.line, "_")
        if isinstance(pat, ir.PMap):
            fields = [
                (Atom("map_field_exact"), pat.line, self.expr_form(k), self._pattern(v))
                for k, v in pat.items
            ]
            return (Atom("map"), pat.line, fields)
        if isinstance(pat, (ir.Int, ir.Float, ir.String, ir.AtomLit, ir.Bool, ir.Nil)):
            return self.expr_form(pat)
        raise EelError(f"cannot generate a pattern for: {pat!r}")

    def _list_pattern(self, pat: ir.PList) -> tuple:
        line = pat.line
        items = list(pat.items)
        tail = None
        if items and isinstance(items[-1], ir.PStar):
            tail = items.pop()
        form = _var(tail.line, self._bind(tail.name)) if tail else (Atom("nil"), line)
        for item in reversed(items):
            form = (Atom("cons"), line, self._pattern(item), form)
        return form

    # --- expressions ---

    def expr_form(self, expr) -> tuple:
        if isinstance(expr, ir.Int):
            return _int(expr.line, expr.value)
        if isinstance(expr, ir.Float):
            return (Atom("float"), expr.line, expr.value)
        if isinstance(expr, ir.String):
            return (
                Atom("bin"),
                expr.line,
                [
                    (
                        Atom("bin_element"),
                        expr.line,
                        (Atom("string"), expr.line, expr.value),
                        Atom("default"),
                        [Atom("utf8")],
                    )
                ],
            )
        if isinstance(expr, ir.AtomLit):
            return _atom(expr.line, expr.name)
        if isinstance(expr, ir.Bool):
            return _atom(expr.line, "true" if expr.value else "false")
        if isinstance(expr, ir.Nil):
            return _atom(expr.line, "nil")
        if isinstance(expr, ir.Var):
            return _var(expr.line, self._resolve(expr.name, expr.line))
        if isinstance(expr, ir.Tuple):
            return (
                Atom("tuple"),
                expr.line,
                [self.expr_form(x) for x in expr.items],
            )
        if isinstance(expr, ir.List):
            return self._list_form(expr)
        if isinstance(expr, ir.Dict):
            return self._dict_form(expr)
        if isinstance(expr, ir.UnaryOp):
            return self._unary(expr)
        if isinstance(expr, ir.BinOp):
            return self._binop(expr)
        if isinstance(expr, ir.BoolOp):
            return self._boolop(expr)
        if isinstance(expr, ir.Call):
            args = [self.expr_form(a) for a in expr.args]
            key = (expr.name, len(expr.args))
            if key in self._defs:
                callee = _var(expr.line, self._defs[key])
            elif any(k[0] == expr.name for k in self._defs):
                have = ", ".join(
                    f"{expr.name}/{a}" for (nm, a) in sorted(self._defs) if nm == expr.name
                )
                raise EelError(
                    f"{expr.line}: function {expr.name}/{len(expr.args)} is not defined (have: {have})"
                )
            elif expr.name in self._scope:
                callee = _var(expr.line, self._resolve(expr.name, expr.line))
            elif expr.name in self._imports:
                module, fname = self._imports[expr.name]
                return (
                    Atom("call"), expr.line,
                    (Atom("remote"), expr.line,
                     (Atom("atom"), 0, Atom(module)), (Atom("atom"), 0, Atom(fname))),
                    args,
                )
            elif self._is_builtin_name(expr.name):
                return self._builtin_form(expr, args)
            elif self._shell_mode:
                hint = ""
                if key in self._later_defs:
                    hint = " (defined below in this cell — move it up)"
                raise EelError(
                    f"{expr.line}: function {expr.name}/{len(expr.args)} is not defined{hint}"
                )
            elif key not in self._module_defs:
                have = sorted(a for (nm, a) in self._module_defs if nm == expr.name)
                hint = (
                    " (have: " + ", ".join(f"{expr.name}/{a}" for a in have) + ")"
                    if have else ""
                )
                raise EelError(
                    f"{expr.line}: function {expr.name}/{len(expr.args)} is not defined{hint}"
                )
            else:
                callee = (Atom("atom"), expr.line, Atom(expr.name))
            return (Atom("call"), expr.line, callee, args)
        if isinstance(expr, ir.Lambda):
            return self._lambda_form(expr)
        if isinstance(expr, ir.Method):
            return self.method_form(expr)

        if isinstance(expr, ir.Receive):
            binder = self._fresh(expr.line, "_eel@r")
            return (Atom("receive"), expr.line, [
                (Atom("clause"), expr.line, [binder], [], [binder]),
            ])

        if isinstance(expr, ir.ListComp):
            return self._listcomp_form(expr)
        if isinstance(expr, ir.DictComp):
            return self._dictcomp_form(expr)
        if isinstance(expr, ir.Merge):
            return _remote_call(
                expr.line, "maps", "merge",
                [self.expr_form(expr.left), self.expr_form(expr.right)],
            )
        raise EelError(f"cannot generate an expression for: {expr!r}")

    # --- built-in functions (redefinable by name) ---

    def _is_builtin_name(self, name: str) -> bool:
        """A builtin name is free: no definition, scope entry or import shadows it."""
        if name in self._scope or name in self._imports:
            return False
        defined = {k[0] for k in self._defs} | {k[0] for k in self._module_defs}
        return name in BUILTINS and name not in defined

    def _builtin_form(self, expr: ir.Call, args: list) -> tuple:
        line, name, arity = expr.line, expr.name, len(args)
        if name == "print":
            if arity != 1:
                raise EelError(
                    f"{line}: print takes exactly one argument, got {arity}"
                )
            self._used_print = True
            return self._helper_call(line, "eel_print", args)
        if name == "zip":
            if arity != 2:
                raise EelError(f"{line}: zip takes exactly two lists, got {arity}")
            self._used_zip = True
            return self._helper_call(line, "eel_zip", args)
        if name == "enumerate":
            if arity not in (1, 2):
                raise EelError(
                    f"{line}: enumerate takes one or two arguments, got {arity}"
                )
            self._used_enumerate = True
            start = args[1] if arity == 2 else _int(line, 0)
            return self._helper_call(line, "eel_enumerate", [start, args[0]])
        if name == "send":
            if arity != 2:
                raise EelError(f"{line}: send takes a pid and a message, got {arity} arguments")
            return _remote_call(line, "erlang", "!", args)
        if name == "self":
            if arity != 0:
                raise EelError(f"{line}: self takes no arguments, got {arity}")
            return _remote_call(line, "erlang", "self", [])
        if name == "spawn":
            if arity != 1:
                raise EelError(f"{line}: spawn takes one function, got {arity} arguments")
            return _remote_call(line, "erlang", "spawn", args)
        if name == "range":
            if not 1 <= arity <= 3:
                raise EelError(
                    f"{line}: range takes one to three arguments, got {arity}"
                )
            self._used_range = True
            start = args[0] if arity >= 2 else _int(line, 0)
            stop = args[1] if arity >= 2 else args[0]
            step = args[2] if arity == 3 else _int(line, 1)
            return self._helper_call(line, "eel_range", [start, stop, step])
        raise EelError(f"{line}: unknown builtin {name}")

    def method_form(self, expr: ir.Method) -> tuple:
        """An stdlib method: dispatch by name and receiver type at runtime."""
        line = expr.line
        name = expr.name
        arity = len(expr.args)
        recv = self.expr_form(expr.receiver)
        args = [self.expr_form(a) for a in expr.args]

        # dict (map)
        if name == "items" and arity == 0:
            return _remote_call(line, "maps", "to_list", [recv])
        if name == "keys" and arity == 0:
            return _remote_call(line, "maps", "keys", [recv])
        if name == "values" and arity == 0:
            return _remote_call(line, "maps", "values", [recv])
        if name == "get" and arity == 1:
            return _remote_call(line, "maps", "get", [args[0], recv, _atom(line, "nil")])
        if name == "get" and arity == 2:
            return _remote_call(line, "maps", "get", [args[0], recv, args[1]])

        # list / tuple
        if name == "count" and arity == 1:
            return self._count_form(line, recv, args[0])

        # str (a binary): string:* also takes chardata — so check is_binary
        if name == "join" and arity == 1:
            self._used_join = True
            return self._as_binary(
                line, recv, name,
                lambda v: _remote_call(
                    line, "erlang", "iolist_to_binary",
                    [self._helper_call(line, "eel_join", [v, args[0]])],
                ),
            )
        if name == "splitlines" and arity == 0:
            return self._as_binary(
                line, recv, name, lambda v: self._splitlines_form(line, v)
            )
        build = _STR_METHODS.get((name, arity))
        if build is not None:
            return self._as_binary(line, recv, name, lambda v: build(line, v, args))

        raise EelError(
            f"{line}: method {name}/{arity} is not supported (standard library only)"
        )

    def _fresh(self, line: int, base: str) -> tuple:
        """A fresh binder name (`@` keeps it clear of user names)."""
        self._temp += 1
        return (Atom("var"), line, Atom(f"{base}{self._temp}"))

    def _as_binary(self, line, recv, name, build) -> tuple:
        binder = self._fresh(line, "_eel@m")
        other = self._fresh(line, "_eel@r")
        check = [[_remote_call(line, "erlang", "is_binary", [binder])]]
        fail = self._bad_receiver(line, name, other)
        return (Atom("case"), line, recv, [
            (Atom("clause"), line, [binder], check, [build(binder)]),
            (Atom("clause"), line, [other], [], [fail]),
        ])

    @staticmethod
    def _bad_argument(line, name, value) -> tuple:
        """`erlang:error({bad_argument, method, value})` — a wrong method argument."""
        return _remote_call(line, "erlang", "error", [
            (Atom("tuple"), line,
             [_atom(line, "bad_argument"), _atom(line, name), value]),
        ])

    @staticmethod
    def _bad_receiver(line, name, value) -> tuple:
        """`erlang:error({bad_receiver, method, value})` — the crash reason is named."""
        return _remote_call(line, "erlang", "error", [
            (Atom("tuple"), line,
             [_atom(line, "bad_receiver"), _atom(line, name), value]),
        ])

    def _splitlines_form(self, line, v) -> tuple:
        """Python `splitlines`: an empty string → [], a trailing newline adds no empty item."""
        empty_bin = (Atom("bin"), line, [])
        global_opt = _list_expr(line, [(Atom("atom"), line, Atom("global"))])
        separators = _list_expr(
            line, [_bin_literal(line, "\r\n"), _bin_literal(line, "\n"), _bin_literal(line, "\r")]
        )
        split = _remote_call(line, "binary", "split", [v, separators, global_opt])
        parts = self._fresh(line, "_eel@p")
        tail = self._fresh(line, "_eel@l")
        drop_last = (Atom("case"), line, _remote_call(line, "lists", "last", [parts]), [
            (Atom("clause"), line, [empty_bin], [],
             [_remote_call(line, "lists", "droplast", [parts])]),
            (Atom("clause"), line, [tail], [], [parts]),
        ])
        non_empty = (Atom("case"), line, split, [
            (Atom("clause"), line, [parts], [], [drop_last]),
        ])
        return (Atom("case"), line, v, [
            (Atom("clause"), line, [empty_bin], [], [_list_expr(line, [])]),
            (Atom("clause"), line, [_var(line, "_")], [], [non_empty]),
        ])

    def _count_form(self, line, recv, arg) -> tuple:
        e = self._fresh(line, "_eel@e")
        l = self._fresh(line, "_eel@l")
        t = self._fresh(line, "_eel@t")
        other = self._fresh(line, "_eel@r")

        def count_of(source):
            # annotation 0: the comprehension is internal, not written by the user
            comp = (
                Atom("lc"), 0, e,
                [(Atom("generate"), 0, e, source), _op(0, "=:=", e, arg)],
            )
            return _remote_call(line, "erlang", "length", [comp])

        return (Atom("case"), line, recv, [
            (Atom("clause"), line, [l], [[_remote_call(line, "erlang", "is_list", [l])]],
             [count_of(l)]),
            (Atom("clause"), line, [t], [[_remote_call(line, "erlang", "is_tuple", [t])]],
             [count_of(_remote_call(line, "erlang", "tuple_to_list", [t]))]),
            (Atom("clause"), line, [other], [], [self._bad_receiver(line, "count", other)]),
        ])

    def _list_form(self, expr: ir.List) -> tuple:
        form = (Atom("nil"), expr.line)
        for item in reversed(expr.items):
            form = (Atom("cons"), expr.line, self.expr_form(item), form)
        return form

    def _dict_form(self, expr: ir.Dict) -> tuple:
        fields = [
            (Atom("map_field_assoc"), expr.line, self.expr_form(k), self.expr_form(v))
            for k, v in expr.items
        ]
        return (Atom("map"), expr.line, fields)

    def _unary(self, expr: ir.UnaryOp) -> tuple:
        operand = self.expr_form(expr.operand)
        if expr.op == "USub":
            return (Atom("op"), expr.line, Atom("-"), operand)
        if expr.op == "Not":
            return (Atom("op"), expr.line, Atom("not"), operand)
        if expr.op == "UAdd":
            return operand
        raise EelError(f"unary operator {expr.op} is not supported")

    def _binop(self, expr: ir.BinOp) -> tuple:
        line = expr.line
        if expr.op in _OP_SYMBOL:
            return _op(
                line,
                _OP_SYMBOL[expr.op],
                self.expr_form(expr.left),
                self.expr_form(expr.right),
            )
        if expr.op == "FloorDiv":
            self._used_floordiv = True
            return self._helper_call(
                line, "eel_floordiv", [self.expr_form(expr.left), self.expr_form(expr.right)]
            )
        if expr.op == "Mod":
            self._used_mod = True
            return self._helper_call(
                line, "eel_mod", [self.expr_form(expr.left), self.expr_form(expr.right)]
            )
        if expr.op == "Pow":
            self._used_pow = True
            return self._helper_call(
                line, "_eel_pow", [self.expr_form(expr.left), self.expr_form(expr.right)]
            )
        raise EelError(f"binary operator {expr.op} is not supported")

    def _boolop(self, expr: ir.BoolOp) -> tuple:
        op = "andalso" if expr.op == "And" else "orelse"
        form = self.expr_form(expr.operands[0])
        for operand in expr.operands[1:]:
            form = (Atom("op"), expr.line, Atom(op), form, self.expr_form(operand))
        return form

    def _lambda_form(self, expr: ir.Lambda) -> tuple:
        saved_scope = dict(self._scope)
        saved_taken = dict(self._taken)
        params = [_var(expr.line, self._bind(p)) for p in expr.params]
        body = self.expr_form(expr.body)
        self._scope = saved_scope
        self._taken = saved_taken
        return (
            Atom("fun"),
            expr.line,
            (Atom("clauses"), [(Atom("clause"), expr.line, params, [], [body])]),
        )

    def _listcomp_form(self, expr: ir.ListComp) -> tuple:
        saved_scope = dict(self._scope)
        saved_taken = dict(self._taken)
        self._taken = {}  # comprehension targets are a local scope, names do not inherit X@N
        quals = self._qualifiers(expr.qualifiers)
        elt = self.expr_form(expr.element)
        self._scope = saved_scope
        self._taken = saved_taken
        return (Atom("lc"), expr.line, elt, quals)

    def _dictcomp_form(self, expr: ir.DictComp) -> tuple:
        saved_scope = dict(self._scope)
        saved_taken = dict(self._taken)
        self._taken = {}
        quals = self._qualifiers(expr.qualifiers)
        key = self.expr_form(expr.key)
        value = self.expr_form(expr.value)
        self._scope = saved_scope
        self._taken = saved_taken
        return (
            Atom("mc"),
            expr.line,
            (Atom("map_field_assoc"), expr.line, key, value),
            quals,
        )

    def _qualifiers(self, quals) -> list:
        out = []
        for q in quals:
            if isinstance(q, ir.Gen):
                self._used_iter = True
                iter_form = self._helper_call(
                    q.line, "eel_iter", [self.expr_form(q.iter)]
                )
                pattern = self._pattern(q.target)
                out.append((Atom("generate"), q.line, pattern, iter_form))
            elif isinstance(q, ir.MapGen):
                map_form = self.expr_form(q.map)
                key_pat = self._pattern(q.key)
                val_pat = self._pattern(q.value)
                out.append(
                    (
                        Atom("m_generate"),
                        q.line,
                        (Atom("map_field_exact"), q.line, key_pat, val_pat),
                        map_form,
                    )
                )
            elif isinstance(q, ir.Filter):
                out.append(self.expr_form(q.expr))
            else:
                raise EelError(f"cannot generate a qualifier for: {q!r}")
        return out

    def iter_helper(self) -> tuple:
        """eel_iter/1: map → keys, list → as is (anything else is a bad_generator)."""
        line = 0
        m = _var(line, "M")
        l = _var(line, "L")
        return (
            Atom("function"),
            line,
            Atom("eel_iter"),
            1,
            [
                (Atom("clause"), line, [m], [[_remote_call(line, "erlang", "is_map", [m])]],
                 [_remote_call(line, "maps", "keys", [m])]),
                (Atom("clause"), line, [l], [], [l]),
            ],
        )

    def print_helpers(self) -> list:
        """eel_print/1 + eel_str/1 (+ tuple/map): the Python-like display for print."""
        line = 0

        def call(name, args):
            return _local_call(line, name, args)

        def str_(form):
            return call("eel_str", [form])

        def is_(kind, var):
            return [[_remote_call(line, "erlang", kind, [var])]]

        def clause(pattern, guards, body):
            return (Atom("clause"), line, [pattern], guards, body)

        def char(code):
            return (Atom("integer"), line, code)

        def joined(items):
            return _remote_call(line, "lists", "join", [_bin_literal(line, ", "), items])

        def each(var, source):
            return (Atom("lc"), line, str_(var),
                    [(Atom("generate"), line, var, source)])

        b, a, i, f, l, t, m, x = (
            _var(line, name) for name in ("B", "A", "I", "F", "L", "T", "M", "X")
        )
        e = _var(line, "E")

        str_fn = (Atom("function"), line, Atom("eel_str"), 1, [
            clause(b, is_("is_binary", b), [b]),
            clause(a, is_("is_atom", a),
                   [_remote_call(line, "erlang", "atom_to_binary", [a, _atom(line, "utf8")])]),
            clause(i, is_("is_integer", i),
                   [_remote_call(line, "erlang", "integer_to_binary", [i])]),
            clause(f, is_("is_float", f),
                   [_remote_call(line, "erlang", "float_to_binary",
                                 [f, _list_expr(line, [_atom(line, "short")])])]),
            clause(l, is_("is_list", l),
                   [_list_expr(line, [char(ord("[")), joined(each(e, l)), char(ord("]"))])]),
            clause(t, is_("is_tuple", t),
                   [call("eel_str_tuple",
                         [_remote_call(line, "erlang", "tuple_to_list", [t])])]),
            clause(m, is_("is_map", m),
                   [call("eel_str_map",
                         [_remote_call(line, "lists", "keysort",
                                       [_int(line, 1),
                                        _remote_call(line, "maps", "to_list", [m])])])]),
            clause(x, [], [_remote_call(line, "io_lib", "format",
                                        [(Atom("string"), line, "~p"),
                                        _list_expr(line, [x])])]),
        ])

        one, items = _var(line, "One"), _var(line, "Items")
        tuple_fn = (Atom("function"), line, Atom("eel_str_tuple"), 1, [
            clause((Atom("nil"), line), [], [_bin_literal(line, "()")]),
            clause((Atom("cons"), line, one, (Atom("nil"), line)), [],
                   [_list_expr(line, [char(ord("(")), str_(one), _bin_literal(line, ",)")])]),
            clause(items, [],
                   [_list_expr(line, [char(ord("(")), joined(each(e, items)), char(ord(")"))])]),
        ])

        pairs, k, v = _var(line, "Pairs"), _var(line, "K"), _var(line, "V")
        pair = _list_expr(line, [str_(k), _bin_literal(line, ": "), str_(v)])
        pair_gen = (Atom("generate"), line, (Atom("tuple"), line, [k, v]), pairs)
        map_fn = (Atom("function"), line, Atom("eel_str_map"), 1, [
            clause((Atom("nil"), line), [], [_bin_literal(line, "{}")]),
            clause(pairs, [], [
                _list_expr(line, [char(ord("{")),
                                  joined((Atom("lc"), line, pair, [pair_gen])),
                                  char(ord("}"))]),
            ]),
        ])

        shown = _var(line, "X")
        print_fn = (Atom("function"), line, Atom("eel_print"), 1, [
            clause(shown, [], [
                _remote_call(line, "io", "format",
                             [(Atom("string"), line, "~ts~n"),
                              _list_expr(line, [str_(shown)])]),
                _atom(line, "nil"),
            ]),
        ])
        return [print_fn, str_fn, tuple_fn, map_fn]

    def join_helper(self) -> tuple:
        """eel_join/2: an iolist of the parts; a non-string is {bad_argument, join, Part}."""
        line = 0
        sep = _var(line, "Sep")
        head = _var(line, "H")
        tail = _var(line, "T")
        one = (Atom("cons"), line, head, (Atom("nil"), line))
        many = (Atom("cons"), line, head, tail)
        is_bin = [[_remote_call(line, "erlang", "is_binary", [head])]]
        return (Atom("function"), line, Atom("eel_join"), 2, [
            (Atom("clause"), line, [_var(line, "_"), (Atom("nil"), line)], [],
             [(Atom("nil"), line)]),
            (Atom("clause"), line, [sep, one], is_bin, [head]),
            (Atom("clause"), line, [sep, many], is_bin,
             [_list_expr(line, [head, sep, _local_call(line, "eel_join", [sep, tail])])]),
            (Atom("clause"), line, [_var(line, "_"), many], [],
             [self._bad_argument(line, "join", head)]),
        ])

    def zip_helper(self) -> tuple:
        """eel_zip/2: lists only, trimmed to the shorter one (Python), otherwise bad_argument."""
        line = 0
        a, b = _var(line, "A"), _var(line, "B")
        is_list = lambda v: _remote_call(line, "erlang", "is_list", [v])  # noqa: E731
        not_list = lambda v: (Atom("op"), line, Atom("not"), is_list(v))  # noqa: E731
        trim = (Atom("atom"), line, Atom("trim"))
        return (Atom("function"), line, Atom("eel_zip"), 2, [
            (Atom("clause"), line, [a, b], [[is_list(a), is_list(b)]],
             [_remote_call(line, "lists", "zip", [a, b, trim])]),
            (Atom("clause"), line, [a, b], [[not_list(a)]],
             [self._bad_argument(line, "zip", a)]),
            (Atom("clause"), line, [a, b], [], [self._bad_argument(line, "zip", b)]),
        ])

    def enumerate_helper(self) -> tuple:
        """eel_enumerate/2: (Start, List) → [{N, X}]; a list only."""
        line = 0
        start, items = _var(line, "Start"), _var(line, "List")
        is_int = lambda v: _remote_call(line, "erlang", "is_integer", [v])  # noqa: E731
        is_list = lambda v: _remote_call(line, "erlang", "is_list", [v])  # noqa: E731
        return (Atom("function"), line, Atom("eel_enumerate"), 2, [
            (Atom("clause"), line, [start, items], [[is_int(start), is_list(items)]],
             [_remote_call(line, "lists", "enumerate", [start, items])]),
            (Atom("clause"), line, [start, items], [[(Atom("op"), line, Atom("not"), is_int(start))]],
             [self._bad_argument(line, "enumerate", start)]),
            (Atom("clause"), line, [start, items], [],
             [self._bad_argument(line, "enumerate", items)]),
        ])

    def range_helper(self) -> tuple:
        """eel_range/3: Python's range (stop excluded, any step, empty ranges are [])."""
        line = 0
        start, stop, step = (
            _var(line, "Start"), _var(line, "Stop"), _var(line, "Step")
        )
        is_int = lambda v: _remote_call(line, "erlang", "is_integer", [v])  # noqa: E731
        not_int = lambda v: (Atom("op"), line, Atom("not"), is_int(v))  # noqa: E731
        greater = lambda x, y: _op(line, ">", x, y)  # noqa: E731
        ge = lambda x, y: _op(line, ">=", x, y)  # noqa: E731
        le = lambda x, y: _op(line, "=<", x, y)  # Erlang spells it =< , not <=
        less = lambda x, y: _op(line, "<", x, y)  # noqa: E731
        zero = _int(line, 0)
        recurse = _local_call(
            line, "eel_range",
            [_op(line, "+", start, step), stop, step],
        )
        return (Atom("function"), line, Atom("eel_range"), 3, [
            (Atom("clause"), line, [start, stop, step], [[not_int(start)]],
             [self._bad_argument(line, "range", start)]),
            (Atom("clause"), line, [start, stop, step], [[not_int(stop)]],
             [self._bad_argument(line, "range", stop)]),
            (Atom("clause"), line, [start, stop, step], [[not_int(step)]],
             [self._bad_argument(line, "range", step)]),
            (Atom("clause"), line, [start, stop, step], [[_op(line, "=:=", step, zero)]],
             [self._bad_argument(line, "range", zero)]),
            # empty ranges: a step pointing the other way — as in Python
            (Atom("clause"), line, [start, stop, step],
             [[greater(step, zero), ge(start, stop)]], [(Atom("nil"), line)]),
            (Atom("clause"), line, [start, stop, step],
             [[less(step, zero), le(start, stop)]], [(Atom("nil"), line)]),
            (Atom("clause"), line, [start, stop, step], [],
             [(Atom("cons"), line, start, recurse)]),
        ])

    def _num_helper(self, name: str, line: int, int_body, float_body) -> tuple:
        a = _var(line, "A")
        b = _var(line, "B")
        is_int = lambda v: _remote_call(line, "erlang", "is_integer", [v])  # noqa: E731
        guard = [[is_int(a), is_int(b)]]
        return (Atom("function"), line, Atom(name), 2, [
            (Atom("clause"), line, [a, b], guard, [int_body]),
            (Atom("clause"), line, [a, b], [], [float_body]),
        ])

    def mod_helper(self) -> tuple:
        line = 0
        a = _var(line, "A")
        b = _var(line, "B")
        mod_int = _op(line, "rem", _op(line, "+", _op(line, "rem", a, b), b), b)
        mod_float = _remote_call(line, "math", "fmod", [
            _op(line, "+", _remote_call(line, "math", "fmod", [a, b]), b), b,
        ])
        return self._num_helper("eel_mod", line, mod_int, mod_float)

    def floordiv_helper(self) -> tuple:
        line = 0
        a = _var(line, "A")
        b = _var(line, "B")
        mod_int = _op(line, "rem", _op(line, "+", _op(line, "rem", a, b), b), b)
        div_int = _op(line, "div", _op(line, "-", a, mod_int), b)
        div_float = _remote_call(line, "erlang", "float",
            [_remote_call(line, "math", "floor", [_op(line, "/", a, b)])])
        return self._num_helper("eel_floordiv", line, div_int, div_float)

    def pow_helpers(self) -> list:
        """The private _eel_pow/2 and _eel_pow_loop/3: int**int (≥0) stays integral,
        otherwise math:pow."""
        line = 0
        a = _var(line, "A")
        b = _var(line, "B")
        is_integer = lambda v: _remote_call(line, "erlang", "is_integer", [v])  # noqa: E731
        guard = [[
            is_integer(a), is_integer(b),
            (Atom("op"), line, Atom(">="), b, _int(line, 0)),
        ]]
        pow_fn = (Atom("function"), line, Atom("_eel_pow"), 2, [
            (Atom("clause"), line, [a, b], guard,
             [_local_call(line, "_eel_pow_loop", [a, b, _int(line, 1)])]),
            (Atom("clause"), line, [a, b], [],
             [_remote_call(line, "math", "pow", [a, b])]),
        ])

        a2 = _var(line, "A")
        n = _var(line, "N")
        acc = _var(line, "Acc")
        even_guard = [[(Atom("op"), line, Atom("=:="),
                        (Atom("op"), line, Atom("rem"), n, _int(line, 2)), _int(line, 0))]]
        loop_fn = (Atom("function"), line, Atom("_eel_pow_loop"), 3, [
            (Atom("clause"), line, [_var(line, "_"), _int(line, 0), acc], [], [acc]),
            (Atom("clause"), line, [a2, n, acc], even_guard,
             [_local_call(line, "_eel_pow_loop",
                          [(Atom("op"), line, Atom("*"), a2, a2),
                           (Atom("op"), line, Atom("div"), n, _int(line, 2)), acc])]),
            (Atom("clause"), line, [a2, n, acc], [],
             [_local_call(line, "_eel_pow_loop",
                          [a2, (Atom("op"), line, Atom("-"), n, _int(line, 1)),
                           (Atom("op"), line, Atom("*"), acc, a2)])]),
        ])
        return [pow_fn, loop_fn]


def module_forms(module: ir.Module) -> list:
    return _Gen().module_forms(module)
