from __future__ import annotations

import ast
import keyword


class LocalNameNormalizer(ast.NodeTransformer):
    def __init__(self, preserve_self_cls: bool = True) -> None:
        self.name_map: dict[str, str] = {}
        self.counter = 0
        self.preserve_self_cls = preserve_self_cls

    def _preserve(self, name: str) -> bool:
        if keyword.iskeyword(name) or name in {"True", "False", "None"}:
            return True
        return self.preserve_self_cls and name in {"self", "cls"}

    def _tok(self, name: str) -> str:
        if name not in self.name_map:
            self.name_map[name] = f"v{self.counter}"
            self.counter += 1
        return self.name_map[name]

    def visit_Name(self, node: ast.Name) -> ast.Name:
        if self._preserve(node.id):
            return node
        return ast.copy_location(ast.Name(id=self._tok(node.id), ctx=node.ctx), node)

    def visit_arg(self, node: ast.arg) -> ast.arg:
        if not self._preserve(node.arg):
            node.arg = self._tok(node.arg)
        return node

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> ast.ExceptHandler:
        self.generic_visit(node)
        if node.name and not self._preserve(node.name):
            node.name = self._tok(node.name)
        return node

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

        ordered_args = (
            list(node.args.posonlyargs)
            + list(node.args.args)
            + list(node.args.kwonlyargs)
        )
        if self.normalize_arg_names:
            for i, arg in enumerate(ordered_args):
                arg.arg = f"arg{i}"
            if node.args.vararg:
                node.args.vararg.arg = "vararg"
            if node.args.kwarg:
                node.args.kwarg.arg = "kwarg"

        if self.strip_annotations:
            for arg in ordered_args:
                arg.annotation = None
                arg.type_comment = None
            if node.args.vararg:
                node.args.vararg.annotation = None
                node.args.vararg.type_comment = None
            if node.args.kwarg:
                node.args.kwarg.annotation = None
                node.args.kwarg.type_comment = None

        if self.normalize_local_names:
            node = LocalNameNormalizer().visit(node)
            node = ast.fix_missing_locations(node)

        if self.normalize_constants:
            node = ConstantNormalizer().visit(node)
            node = ast.fix_missing_locations(node)

        return node
