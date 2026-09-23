from __future__ import annotations

import ast
import keyword
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


def _parameter_names(node: _FuncNode | ast.Lambda) -> frozenset[str]:
    args = node.args
    params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if args.vararg:
        params.append(args.vararg)
    if args.kwarg:
        params.append(args.kwarg)
    return frozenset(param.arg for param in params)


class LocalNameNormalizer(ast.NodeTransformer):
    """Replace names bound in the visited scopes with positional placeholders.

    With ``bound`` given, only names bound in that scope or in nested scopes
    entered during the visit are renamed; module-level helpers, imported
    names, and builtins are kept, so functions that call different helpers
    do not become equivalent. Without ``bound`` every non-keyword name is
    renamed. Names bound in a nested scope shadow outer bindings; with
    ``rename_nested`` false they are left alone, which is how parameter-only
    renaming avoids touching locals of nested functions or lambdas.
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
        # Innermost scope last. Each entry records whether names bound in
        # that scope are renamed or merely shadow outer bindings.
        self.scopes: list[tuple[frozenset[str], bool]] = (
            [(bound, True)] if bound is not None else []
        )

    def _preserve(self, name: str) -> bool:
        if keyword.iskeyword(name) or name in _PRESERVED or is_placeholder(name):
            return True
        if self.preserve_self_cls and name in {"self", "cls"}:
            return True
        if self.unrestricted:
            return False
        for names, renamable in reversed(self.scopes):
            if name in names:
                return not renamable
        return True

    def _tok(self, name: str) -> str:
        if name not in self.name_map:
            self.name_map[name] = placeholder(self.prefix, self.counter)
            self.counter += 1
        return self.name_map[name]

    def _rename(self, name: str | None) -> str | None:
        if name and not self._preserve(name):
            return self._tok(name)
        return name

    def _scoped(self, node: ast.AST, names: frozenset[str]) -> ast.AST:
        self.scopes.append((names, self.rename_nested))
        try:
            self.generic_visit(node)
        finally:
            self.scopes.pop()
        return node

    def visit_Name(self, node: ast.Name) -> ast.Name:
        if self._preserve(node.id):
            return node
        return ast.copy_location(ast.Name(id=self._tok(node.id), ctx=node.ctx), node)

    def visit_arg(self, node: ast.arg) -> ast.arg:
        self.generic_visit(node)
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

    def visit_Lambda(self, node: ast.Lambda) -> ast.AST:
        return self._scoped(node, _parameter_names(node))

    def visit_ListComp(self, node: ast.ListComp) -> ast.AST:
        return self._scoped(node, _comprehension_targets(node))

    def visit_SetComp(self, node: ast.SetComp) -> ast.AST:
        return self._scoped(node, _comprehension_targets(node))

    def visit_DictComp(self, node: ast.DictComp) -> ast.AST:
        return self._scoped(node, _comprehension_targets(node))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> ast.AST:
        return self._scoped(node, _comprehension_targets(node))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._nested_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._nested_scope(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        return self._nested_scope(node)

    def _nested_scope(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ) -> ast.AST:
        node.name = self._rename(node.name) or node.name
        return self._scoped(node, bound_names(node))

    def visit_Global(self, node: ast.Global) -> ast.Global:  # preserve semantics
        return node

    def visit_Nonlocal(self, node: ast.Nonlocal) -> ast.Nonlocal:  # preserve semantics
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
        bound = bound_names(node)
        params = _parameter_names(node)
        node = self.generic_visit(node)  # type: ignore[assignment]

        if self.strip_docstrings and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                node.body = node.body[1:]

        if not self.preserve_function_name:
            node.name = "__func__"

        if self.strip_decorators:
            node.decorator_list = []

        if self.strip_annotations:
            node.returns = None
            node.type_comment = None
            for arg in ast.walk(node.args):
                if isinstance(arg, ast.arg):
                    arg.annotation = None
                    arg.type_comment = None

        # Parameters and their references are renamed through one mapping so
        # that a parameter's name never matters, at either tier.
        if self.normalize_local_names:
            LocalNameNormalizer(bound=bound).generic_visit(node)
        elif self.normalize_arg_names:
            LocalNameNormalizer(
                bound=params, prefix="arg", rename_nested=False
            ).generic_visit(node)

        if self.normalize_constants:
            node = ConstantNormalizer().visit(node)

        return node
