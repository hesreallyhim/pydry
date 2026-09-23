"""Statement-level canonical forms.

Every function is flattened into a pre-order sequence of ``StmtToken`` values,
one per statement. Compound statements contribute a header token (the ``if``
test, the ``for`` target and iterable, and so on) followed by their children at
the next depth. Each token carries three canonical strings:

- ``raw``: docstrings, annotations, and decorators removed, nothing else.
- ``names``: additionally, names bound inside the function (or in a
  comprehension or lambda within the statement) are replaced by positional
  placeholders, numbered per statement.
- ``full``: additionally, constants are replaced by typed placeholders.
- ``loose``: local names, literals, and ``UPPER_CASE`` module constants
  collapse to a single placeholder, so a parameter, a literal, and a named
  constant in the same position compare equal.

Comparing two aligned statements at these three tiers tells the engine whether
they are identical, differ only by local names, or differ only by constants.
The ``full`` form is what near-match alignment and block clone hashing use.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .normalize import (
    ConstantNormalizer,
    LocalNameNormalizer,
    bound_names,
    is_placeholder,
)

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


def _looks_like_constant(name: str) -> bool:
    """``UPPER_CASE`` identifiers are treated as constants by convention."""

    return len(name) > 1 and name.isupper()


class _SlotNormalizer(ast.NodeTransformer):
    """Collapse placeholders and constants into one slot marker."""

    def visit_Name(self, node: ast.Name) -> ast.Name:
        if is_placeholder(node.id) or _looks_like_constant(node.id):
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


def _lineno(node: ast.AST) -> int:
    """Line of ``node``, or of its first located descendant.

    ``match_case`` carries no location of its own; its pattern does.
    """

    for candidate in ast.walk(node):
        line = getattr(candidate, "lineno", None)
        if line:
            return int(line)
    return 0


def _first_child_line(stmt: ast.stmt) -> int | None:
    for name in ("body", "handlers", "cases"):
        children = getattr(stmt, name, None)
        if children:
            return _lineno(children[0]) or None
    return None


def _token_for(stmt: ast.stmt, depth: int, bound: frozenset[str]) -> StmtToken | None:
    if _is_string_expr(stmt):
        return None
    # Locations are never dumped, so there is no need to repair them after
    # each transformer pass.
    header = _AnnotationStripper().visit(_header(stmt))
    raw = ast.dump(header, annotate_fields=False)
    renamed = LocalNameNormalizer(bound=bound).visit(header)
    names = ast.dump(renamed, annotate_fields=False)
    abstracted = ConstantNormalizer().visit(renamed)
    full = ast.dump(abstracted, annotate_fields=False)
    loose = ast.dump(_SlotNormalizer().visit(abstracted), annotate_fields=False)

    is_compound = any(getattr(stmt, name, None) for name in _COMPOUND_FIELDS)
    calls = sum(1 for node in ast.walk(header) if isinstance(node, ast.Call))
    lineno = _lineno(stmt)
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
        if isinstance(stmt, (ast.Try, ast.TryStar)):
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
