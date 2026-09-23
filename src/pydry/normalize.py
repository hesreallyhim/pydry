from __future__ import annotations

import ast
import keyword
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_FuncNode = ast.FunctionDef | ast.AsyncFunctionDef
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_PRESERVED = frozenset({"True", "False", "None"})


def placeholder(prefix: str, index: int) -> str:
    """A generated name that no Python identifier can collide with."""

    return f"«{prefix}{index}»"


def is_placeholder(name: str) -> bool:
    return name.startswith("«") and name.endswith("»")


def iter_scope(node: ast.AST) -> Iterator[ast.AST]:
    """Yield the nodes that belong to ``node``'s own scope.

    Nested functions, classes, lambdas, and comprehensions are yielded (their
    names or the node itself bind in this scope) but not descended into.
    Assignment expressions inside a comprehension bind in the enclosing
    scope, so their targets are yielded as well.
    """

    stack = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, (*_SCOPE_NODES, ast.Lambda)):
            continue
        if isinstance(current, _COMPREHENSIONS):
            for inner in ast.walk(current):
                if isinstance(inner, ast.NamedExpr):
                    yield inner.target
            continue
        stack.extend(ast.iter_child_nodes(current))


def _collect_bindings(nodes: Iterator[ast.AST], owner: ast.AST) -> frozenset[str]:
    names: set[str] = set()
    declared: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                names.add(node.name)
        elif isinstance(node, _SCOPE_NODES):
            if node is not owner:
                names.add(node.name)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            declared.update(node.names)
    return frozenset(names - declared)


def bound_names(scope: ast.AST) -> frozenset[str]:
    """Names bound directly in ``scope``, excluding global/nonlocal ones.

    Comprehension targets and lambda parameters belong to their own scopes
    and are not included; nested function and class names are.
    """

    return _collect_bindings(iter_scope(scope), scope)


def all_bindings(fn: _FuncNode) -> frozenset[str]:
    """Every name bound anywhere inside ``fn``, including nested scopes."""

    return _collect_bindings(ast.walk(fn), fn)


def _comprehension_targets(node: ast.AST) -> frozenset[str]:
    names = set()
    for generator in getattr(node, "generators", []):
        for sub in ast.walk(generator.target):
            if isinstance(sub, ast.Name):
                names.add(sub.id)
    return frozenset(names)


def _parameters(args: ast.arguments) -> list[ast.arg]:
    params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if args.vararg:
        params.append(args.vararg)
    if args.kwarg:
        params.append(args.kwarg)
    return params


def _parameter_names(node: _FuncNode | ast.Lambda) -> frozenset[str]:
    return frozenset(param.arg for param in _parameters(node.args))


@dataclass
class _NameScope:
    bound: frozenset[str]
    renamed: frozenset[str]
    names: dict[str, str] = field(default_factory=dict)
    globals: frozenset[str] = frozenset()
    is_class: bool = False


class LocalNameNormalizer(ast.NodeTransformer):
    """Replace names bound in the visited scopes with positional placeholders.

    With ``bound`` given, only names bound in that scope or in nested scopes
    entered during the visit are renamed; module-level helpers, imported
    names, and builtins are kept, so functions that call different helpers
    do not become equivalent. Without ``bound`` every non-keyword name is
    renamed. Names bound in a nested scope shadow outer bindings; with
    ``rename_nested`` false, only named-function parameters are renamed in
    nested scopes; other locals and lambda parameters are left alone.
    Placeholders use characters that cannot appear in identifiers, so a
    source name such as ``arg0`` or ``v0`` can never collide with them.
    """

    def __init__(
        self,
        preserve_self_cls: bool = True,
        bound: frozenset[str] | None = None,
        prefix: str = "v",
        rename_nested: bool = True,
    ) -> None:
        self.name_map: dict[str, str] = {}
        self.counter = 0
        self.preserve_self_cls = preserve_self_cls
        self.prefix = prefix
        self.unrestricted = bound is None
        self.rename_nested = rename_nested
        # A spelling can denote different bindings in different scopes.
        # Maps are scoped; the counter is shared for the entire traversal.
        self.scopes = [_NameScope(bound, bound)] if bound is not None else []

    def _preserve(self, name: str) -> bool:
        if keyword.iskeyword(name) or name in _PRESERVED or is_placeholder(name):
            return True
        if self.preserve_self_cls and name in {"self", "cls"}:
            return True
        for scope in reversed(self.scopes):
            if scope.is_class and scope is not self.scopes[-1]:
                continue
            if name in scope.globals:
                return True
            if name in scope.bound:
                return name not in scope.renamed
        return not self.unrestricted

    def _tok(self, name: str) -> str:
        mapping = self.name_map
        for scope in reversed(self.scopes):
            if scope.is_class and scope is not self.scopes[-1]:
                continue
            if name in scope.bound:
                mapping = scope.names
                break
        if name not in mapping:
            mapping[name] = placeholder(self.prefix, self.counter)
            self.counter += 1
        return mapping[name]

    def _rename(self, name: str | None) -> str | None:
        if name and not self._preserve(name):
            return self._tok(name)
        return name

    def visit_Name(self, node: ast.Name) -> ast.Name:
        if self._preserve(node.id):
            return node
        return ast.copy_location(ast.Name(id=self._tok(node.id), ctx=node.ctx), node)

    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.arg = self._rename(node.arg) or node.arg
        return node

    def _rename_field(self, node: ast.AST, field: str) -> ast.AST:
        """Visit children, then rename the binding stored in ``field``."""

        self.generic_visit(node)
        setattr(node, field, self._rename(getattr(node, field)))
        return node

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> ast.AST:
        return self._rename_field(node, "name")

    def visit_MatchAs(self, node: ast.MatchAs) -> ast.AST:
        return self._rename_field(node, "name")

    def visit_MatchStar(self, node: ast.MatchStar) -> ast.AST:
        return self._rename_field(node, "name")

    def visit_MatchMapping(self, node: ast.MatchMapping) -> ast.AST:
        return self._rename_field(node, "rest")

    def _visit_defaults(self, args: ast.arguments) -> None:
        # Defaults and annotations evaluate outside the function's scope.
        args.defaults = [self.visit(default) for default in args.defaults]
        args.kw_defaults = [
            self.visit(default) if default is not None else None
            for default in args.kw_defaults
        ]
        for arg in _parameters(args):
            if arg.annotation is not None:
                arg.annotation = self.visit(arg.annotation)

    def _visit_parameters(self, args: ast.arguments) -> None:
        for arg in _parameters(args):
            self.visit_arg(arg)

    def visit_Lambda(self, node: ast.Lambda) -> ast.AST:
        self._visit_defaults(node.args)
        names = bound_names(node)
        renamed = names if self.rename_nested else frozenset()
        self.scopes.append(_NameScope(names, renamed))
        try:
            self._visit_parameters(node.args)
            node.body = self.visit(node.body)
        finally:
            self.scopes.pop()
        return node

    def visit_ListComp(self, node: ast.ListComp) -> ast.AST:
        return self._comprehension_scope(node)

    def visit_SetComp(self, node: ast.SetComp) -> ast.AST:
        return self._comprehension_scope(node)

    def visit_DictComp(self, node: ast.DictComp) -> ast.AST:
        return self._comprehension_scope(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> ast.AST:
        return self._comprehension_scope(node)

    def _comprehension_scope(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> ast.AST:
        # Only the first iterable is evaluated outside the comprehension.
        first = node.generators[0]
        first.iter = self.visit(first.iter)
        names = _comprehension_targets(node)
        renamed = names if self.rename_nested else frozenset()
        self.scopes.append(_NameScope(names, renamed))
        try:
            for generator in node.generators:
                if generator is not first:
                    generator.iter = self.visit(generator.iter)
                generator.target = self.visit(generator.target)
                generator.ifs = [self.visit(item) for item in generator.ifs]
            if isinstance(node, ast.DictComp):
                node.key = self.visit(node.key)
                node.value = self.visit(node.value)
            else:
                node.elt = self.visit(node.elt)
        finally:
            self.scopes.pop()
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._function_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._function_scope(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        node.name = self._rename(node.name) or node.name
        node.bases = [self.visit(base) for base in node.bases]
        node.keywords = [self.visit(item) for item in node.keywords]
        node.decorator_list = [self.visit(item) for item in node.decorator_list]
        names = bound_names(node)
        renamed = names if self.rename_nested else frozenset()
        self.scopes.append(_NameScope(names, renamed, is_class=True))
        try:
            node.body = [self.visit(statement) for statement in node.body]
        finally:
            self.scopes.pop()
        return node

    def _function_scope(self, node: _FuncNode) -> ast.AST:
        node.name = self._rename(node.name) or node.name
        self._visit_defaults(node.args)
        node.decorator_list = [self.visit(item) for item in node.decorator_list]
        if node.returns is not None:
            node.returns = self.visit(node.returns)
        names = bound_names(node)
        renamed = names if self.rename_nested else _parameter_names(node)
        globals_ = frozenset(
            name
            for child in iter_scope(node)
            if isinstance(child, ast.Global)
            for name in child.names
        )
        self.scopes.append(_NameScope(names, renamed, globals=globals_))
        try:
            self._visit_parameters(node.args)
            node.body = [self.visit(statement) for statement in node.body]
        finally:
            self.scopes.pop()
        return node

    def visit_Global(self, node: ast.Global) -> ast.Global:  # preserve semantics
        return node

    def visit_Nonlocal(self, node: ast.Nonlocal) -> ast.Nonlocal:
        if self.scopes:
            node.names = [self._rename(name) or name for name in node.names]
        return node


class ConstantNormalizer(ast.NodeTransformer):
    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        v = node.value
        rep: str | bytes | bool | int | float | complex | None
        if isinstance(v, str):
            rep = "__str__"
        elif isinstance(v, bytes):
            rep = b"__bytes__"
        elif isinstance(v, bool) or v is None:
            rep = v
        elif isinstance(v, int):
            rep = 0
        elif isinstance(v, float):
            rep = 0.0
        elif isinstance(v, complex):
            rep = 0j
        else:
            rep = "__const__"
        return ast.copy_location(ast.Constant(value=rep), node)


class FunctionNormalizer(ast.NodeTransformer):
    def __init__(
        self,
        *,
        strip_docstrings: bool = True,
        strip_decorators: bool = True,
        normalize_arg_names: bool = True,
        strip_annotations: bool = True,
        normalize_local_names: bool = False,
        normalize_constants: bool = False,
        preserve_function_name: bool = False,
    ) -> None:
        self.strip_docstrings = strip_docstrings
        self.strip_decorators = strip_decorators
        self.normalize_arg_names = normalize_arg_names
        self.strip_annotations = strip_annotations
        self.normalize_local_names = normalize_local_names
        self.normalize_constants = normalize_constants
        self.preserve_function_name = preserve_function_name
        self._depth = 0

    def visit_FunctionDef(
        self, node: ast.FunctionDef
    ) -> ast.FunctionDef | ast.AsyncFunctionDef:
        return self._normalize(node)

    def visit_AsyncFunctionDef(
        self, node: ast.AsyncFunctionDef
    ) -> ast.FunctionDef | ast.AsyncFunctionDef:
        return self._normalize(node)

    def _normalize(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> ast.FunctionDef | ast.AsyncFunctionDef:
        self._depth += 1
        try:
            node = self.generic_visit(node)  # type: ignore[assignment]
        finally:
            self._depth -= 1

        if self.strip_docstrings and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                node.body = node.body[1:]

        if self.strip_decorators:
            node.decorator_list = []

        if self.strip_annotations:
            node.returns = None
            node.type_comment = None
            for arg in ast.walk(node.args):
                if isinstance(arg, ast.arg):
                    arg.annotation = None
                    arg.type_comment = None

        # Normalize the whole tree once: closure references and nested
        # parameters must share a traversal, not independent placeholder maps.
        if self._depth == 0:
            if self.normalize_local_names or self.normalize_arg_names:
                LocalNameNormalizer(
                    bound=frozenset(),
                    prefix="v" if self.normalize_local_names else "arg",
                    rename_nested=self.normalize_local_names,
                ).visit(node)
            if not self.preserve_function_name:
                for child in ast.walk(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        child.name = "__func__"

        if self.normalize_constants:
            node = ConstantNormalizer().visit(node)

        return node
