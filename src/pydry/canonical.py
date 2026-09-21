"""Statement-level canonical forms.

Every function is flattened into a pre-order sequence of ``StmtToken`` values,
one per statement. Compound statements contribute a header token (the ``if``
test, the ``for`` target and iterable, and so on) followed by their children at
the next depth. Each token carries three canonical strings:

- ``raw``: docstrings, annotations, and decorators removed, nothing else.
- ``names``: additionally, names bound inside the function are replaced by
  positional placeholders, numbered per statement.
- ``full``: additionally, constants are replaced by typed placeholders.
- ``loose``: local names and constants collapse to a single placeholder, so a
  parameter and a literal in the same position compare equal.

Comparing two aligned statements at these three tiers tells the engine whether
they are identical, differ only by local names, or differ only by constants.
The ``full`` form is what near-match alignment and block clone hashing use.
"""

from __future__ import annotations

import ast
import copy
import keyword
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .normalize import ConstantNormalizer

if TYPE_CHECKING:
    from collections.abc import Iterator

_FuncNode = ast.FunctionDef | ast.AsyncFunctionDef
_COMPOUND_FIELDS = ("body", "orelse", "finalbody", "handlers", "cases")
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_TRIVIAL_KINDS = frozenset({"Pass", "Raise", "Return", "Expr"})


@dataclass(frozen=True)
class StmtToken:
    """One statement of a function in canonical form."""

    depth: int
    kind: str
    raw: str
    names: str
    full: str
    loose: str
    lineno: int
    end_lineno: int
    weight: int

    @property
    def key(self) -> tuple[int, str]:
        return (self.depth, self.full)

    @property
    def loose_key(self) -> tuple[int, str]:
        return (self.depth, self.loose)


def bound_names(fn: _FuncNode) -> frozenset[str]:
    """Names bound anywhere inside the function, excluding global/nonlocal."""

    names: set[str] = set()
    declared: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                names.add(node.name)
        elif isinstance(node, _SCOPE_NODES):
            if node is not fn:
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


class _StatementNormalizer(ast.NodeTransformer):
    """Replace function-local names with per-statement placeholders."""

    def __init__(self, bound: frozenset[str]) -> None:
        self.bound = bound
        self.mapping: dict[str, str] = {}

    def _placeholder(self, name: str) -> str:
        if name not in self.mapping:
            self.mapping[name] = f"v{len(self.mapping)}"
        return self.mapping[name]

    def _is_local(self, name: str) -> bool:
        if keyword.iskeyword(name) or name in {"self", "cls"}:
            return False
        return name in self.bound

    def visit_Name(self, node: ast.Name) -> ast.Name:
        if not self._is_local(node.id):
            return node
        return ast.copy_location(
            ast.Name(id=self._placeholder(node.id), ctx=node.ctx), node
        )

    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.annotation = None
        node.type_comment = None
        if self._is_local(node.arg):
            node.arg = self._placeholder(node.arg)
        return node

    def _rename_field(self, node: ast.AST, field: str) -> ast.AST:
        """Visit children, then rename the binding stored in ``field``."""

        self.generic_visit(node)
        name = getattr(node, field)
        if name and self._is_local(name):
            setattr(node, field, self._placeholder(name))
        return node

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> ast.AST:
        return self._rename_field(node, "name")

    def visit_MatchAs(self, node: ast.MatchAs) -> ast.AST:
        return self._rename_field(node, "name")

    def visit_MatchStar(self, node: ast.MatchStar) -> ast.AST:
        return self._rename_field(node, "name")

    def visit_MatchMapping(self, node: ast.MatchMapping) -> ast.AST:
        return self._rename_field(node, "rest")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._rename_field(node, "name")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._rename_field(node, "name")

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        return self._rename_field(node, "name")

    def visit_alias(self, node: ast.alias) -> ast.alias:
        return node

    def visit_Global(self, node: ast.Global) -> ast.Global:
        return node

    def visit_Nonlocal(self, node: ast.Nonlocal) -> ast.Nonlocal:
        return node


class _SlotNormalizer(ast.NodeTransformer):
    """Collapse placeholders and constants into one slot marker."""

    def visit_Name(self, node: ast.Name) -> ast.Name:
        if node.id.startswith("v") and node.id[1:].isdigit():
            return ast.copy_location(ast.Name(id="_", ctx=node.ctx), node)
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if node.value is None or isinstance(node.value, bool):
            return node
        return ast.copy_location(ast.Name(id="_", ctx=ast.Load()), node)


class _AnnotationStripper(ast.NodeTransformer):
    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.annotation = None
        node.type_comment = None
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._strip(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._strip(node)

    def _strip(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.AST:
        self.generic_visit(node)
        node.decorator_list = []
        node.returns = None
        node.type_comment = None
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        self.generic_visit(node)
        node.decorator_list = []
        return node


def _is_string_expr(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _header(stmt: ast.stmt) -> ast.stmt:
    shallow = copy.copy(stmt)
    for name in _COMPOUND_FIELDS:
        if hasattr(shallow, name):
            setattr(shallow, name, [])
    if isinstance(shallow, ast.AnnAssign):
        if shallow.value is None:
            return copy.deepcopy(shallow)
        return ast.copy_location(
            ast.Assign(targets=[shallow.target], value=shallow.value), stmt
        )
    return copy.deepcopy(shallow)


def _first_child_line(stmt: ast.stmt) -> int | None:
    for name in ("body", "handlers", "cases"):
        children = getattr(stmt, name, None)
        if children:
            first = children[0]
            return int(getattr(first, "lineno", 0)) or None
    return None


def _token_for(stmt: ast.stmt, depth: int, bound: frozenset[str]) -> StmtToken | None:
    if _is_string_expr(stmt):
        return None
    header = _header(stmt)
    header = ast.fix_missing_locations(_AnnotationStripper().visit(header))
    raw = ast.dump(header, annotate_fields=False)
    renamed = ast.fix_missing_locations(_StatementNormalizer(bound).visit(header))
    names = ast.dump(renamed, annotate_fields=False)
    abstracted = ast.fix_missing_locations(ConstantNormalizer().visit(renamed))
    full = ast.dump(abstracted, annotate_fields=False)
    loose = ast.dump(
        ast.fix_missing_locations(_SlotNormalizer().visit(abstracted)),
        annotate_fields=False,
    )

    is_compound = any(getattr(stmt, name, None) for name in _COMPOUND_FIELDS)
    calls = sum(1 for node in ast.walk(header) if isinstance(node, ast.Call))
    lineno = int(getattr(stmt, "lineno", 0))
    end_lineno = int(getattr(stmt, "end_lineno", None) or lineno)
    if is_compound:
        child_line = _first_child_line(stmt)
        if child_line is not None:
            end_lineno = max(lineno, child_line - 1)
    return StmtToken(
        depth=depth,
        kind=type(stmt).__name__,
        raw=raw,
        names=names,
        full=full,
        loose=loose,
        lineno=lineno,
        end_lineno=end_lineno,
        weight=calls + int(is_compound),
    )


def _marker(kind: str, depth: int, children: list[ast.stmt]) -> StmtToken:
    lineno = int(getattr(children[0], "lineno", 0))
    return StmtToken(
        depth=depth,
        kind=kind,
        raw=kind,
        names=kind,
        full=kind,
        loose=kind,
        lineno=max(1, lineno - 1),
        end_lineno=max(1, lineno - 1),
        weight=0,
    )


def _walk(
    stmts: list[ast.stmt], depth: int, bound: frozenset[str]
) -> Iterator[StmtToken]:
    for stmt in stmts:
        token = _token_for(stmt, depth, bound)
        if token is None:
            continue
        yield token
        if isinstance(stmt, _SCOPE_NODES):
            # Nested scopes are profiled as functions in their own right.
            continue
        if isinstance(stmt, ast.Try):
            yield from _walk(stmt.body, depth + 1, bound)
            for handler in stmt.handlers:
                handler_token = _token_for(handler, depth, bound)  # type: ignore[arg-type]
                if handler_token is not None:
                    yield handler_token
                yield from _walk(handler.body, depth + 1, bound)
            if stmt.orelse:
                yield _marker("Else", depth, stmt.orelse)
                yield from _walk(stmt.orelse, depth + 1, bound)
            if stmt.finalbody:
                yield _marker("Finally", depth, stmt.finalbody)
                yield from _walk(stmt.finalbody, depth + 1, bound)
            continue
        if isinstance(stmt, ast.Match):
            for case in stmt.cases:
                case_token = _token_for(case, depth + 1, bound)  # type: ignore[arg-type]
                if case_token is not None:
                    yield case_token
                yield from _walk(case.body, depth + 2, bound)
            continue
        body = getattr(stmt, "body", None)
        if isinstance(body, list):
            yield from _walk(body, depth + 1, bound)
        orelse = getattr(stmt, "orelse", None)
        if isinstance(orelse, list) and orelse:
            yield _marker("Else", depth, orelse)
            yield from _walk(orelse, depth + 1, bound)


def statement_tokens(fn: _FuncNode) -> list[StmtToken]:
    """Flatten a function body into canonical statement tokens."""

    body = list(fn.body)
    if body and _is_string_expr(body[0]):
        body = body[1:]
    return list(_walk(body, 0, bound_names(fn)))


def is_trivial(tokens: list[StmtToken]) -> bool:
    """Bodies that are not worth reporting: stubs, accessors, boilerplate.

    A body is trivial when it consists only of ``pass``, ``raise``, or
    call-free ``return``/expression statements, or when it has at most three
    statements and no calls or control flow at all (attribute assignment in
    ``__init__``, property getters, and similar).
    """

    if not tokens:
        return True
    if all(
        token.kind in _TRIVIAL_KINDS and (token.kind == "Raise" or token.weight == 0)
        for token in tokens
    ):
        return True
    return len(tokens) <= 3 and all(token.weight == 0 for token in tokens)


def sequence_similarity(shared: int, length_a: int, length_b: int) -> float:
    if length_a + length_b == 0:
        return 1.0
    return (2 * shared) / (length_a + length_b)


def lcs_alignment(
    a: list[tuple[int, str]], b: list[tuple[int, str]]
) -> list[tuple[int, int]]:
    """Indices of a longest common subsequence between two token key lists."""

    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return []
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row = table[i]
        below = table[i + 1]
        ai = a[i]
        for j in range(m - 1, -1, -1):
            if ai == b[j]:
                row[j] = below[j + 1] + 1
            else:
                row[j] = below[j] if below[j] >= row[j + 1] else row[j + 1]
    pairs: list[tuple[int, int]] = []
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j]:
            pairs.append((i, j))
            i += 1
            j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def bag_upper_bound(
    counts_a: dict[tuple[int, str], int],
    counts_b: dict[tuple[int, str], int],
    length_a: int,
    length_b: int,
) -> float:
    """Cheap bound on sequence similarity from multiset intersection."""

    small, large = (
        (counts_a, counts_b)
        if len(counts_a) <= len(counts_b)
        else (
            counts_b,
            counts_a,
        )
    )
    shared = sum(min(count, large.get(key, 0)) for key, count in small.items())
    return sequence_similarity(shared, length_a, length_b)
