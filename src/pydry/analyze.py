from __future__ import annotations

import ast
import os
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .models import FunctionOccurrence
from .normalize import FunctionNormalizer

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable

_FuncNode = ast.FunctionDef | ast.AsyncFunctionDef

SIDE_EFFECT_CALLS = {
    "print",
    "open",
    "write",
    "send",
    "post",
    "put",
    "delete",
    "remove",
    "unlink",
    "save",
    "commit",
}
CONTROL_FLOW_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.Match,
)
STMT_TYPES = (
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.Return,
    ast.Expr,
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.Raise,
    ast.Assert,
    ast.Pass,
    ast.Break,
    ast.Continue,
    ast.Import,
    ast.ImportFrom,
    ast.Delete,
    ast.Match,
    ast.Yield,
    ast.YieldFrom,
)

DEFAULT_EXCLUDED_DIRS = {
    "__pycache__",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "site-packages",
    "build",
    "dist",
    ".eggs",
}


def iter_python_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = sorted(d for d in dirnames if d not in DEFAULT_EXCLUDED_DIRS)
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                path = Path(dirpath, filename)
                if path.is_file():
                    yield path


def build_qualname(parents: list[str], name: str) -> str:
    return ".".join([*parents, name]) if parents else name


def iter_functions(
    module: ast.Module, top_level_only: bool = False
) -> Generator[tuple[_FuncNode, list[str], bool]]:
    if top_level_only:
        for node in module.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield node, [], False
        return

    def walk(
        nodes: list[ast.stmt], parents: list[str], container_kind: str
    ) -> Generator[tuple[_FuncNode, list[str], bool]]:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                is_class_method = container_kind == "class"
                yield node, parents, is_class_method
                yield from walk(node.body, [*parents, node.name], "function")
            elif isinstance(node, ast.ClassDef):
                yield from walk(node.body, [*parents, node.name], "class")

    yield from walk(module.body, [], "module")


def param_count(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    return (
        len(fn.args.posonlyargs)
        + len(fn.args.args)
        + len(fn.args.kwonlyargs)
        + int(fn.args.vararg is not None)
        + int(fn.args.kwarg is not None)
    )


def is_method(parents: list[str]) -> bool:
    return bool(parents)


def canonicalize(fn: _FuncNode, **opts: Any) -> str:
    cloned = ast.fix_missing_locations(ast.parse(ast.unparse(fn)).body[0])
    norm = FunctionNormalizer(**opts)
    cloned = ast.fix_missing_locations(norm.visit(cloned))
    return ast.dump(cloned, annotate_fields=True, include_attributes=False)


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        parts = []
        cur: ast.expr = f
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    return "<dynamic>"


def _literal_token(value: object) -> str:
    if isinstance(value, str):
        return f"str:{value}"
    if isinstance(value, bytes):
        return f"bytes:{value!r}"
    if value is None:
        return "none"
    if isinstance(value, bool):
        return f"bool:{value}"
    if isinstance(value, int):
        return f"int:{value}"
    if isinstance(value, float):
        return f"float:{value!r}"
    if isinstance(value, complex):
        return f"complex:{value!r}"
    return f"type:{type(value).__name__}"


def _stmt_sequence(fn: _FuncNode) -> list[str]:
    seq = []
    for n in ast.walk(fn):
        if isinstance(n, STMT_TYPES):
            seq.append(type(n).__name__)
    return seq


def _counter_jaccard(a: Counter[str], b: Counter[str]) -> float:
    keys = set(a) | set(b)
    if not keys:
        return 1.0
    inter = sum(min(a[k], b[k]) for k in keys)
    union = sum(max(a[k], b[k]) for k in keys)
    return inter / union if union else 1.0


def _lcs_ratio(a: list[str], b: list[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    longer = a
    shorter = b
    if len(shorter) > len(longer):
        longer, shorter = shorter, longer

    prev = [0] * (len(shorter) + 1)
    for token in longer:
        current = [0] * (len(shorter) + 1)
        for j, short_token in enumerate(shorter, start=1):
            if token == short_token:
                current[j] = prev[j - 1] + 1
            else:
                current[j] = max(prev[j], current[j - 1])
        prev = current

    lcs = prev[-1]
    return (2 * lcs) / (len(a) + len(b))


def extract_features(fn: _FuncNode) -> dict[str, Any]:
    node_types = Counter(type(n).__name__ for n in ast.walk(fn))
    stmt_seq = _stmt_sequence(fn)
    call_names = Counter(_call_name(n) for n in ast.walk(fn) if isinstance(n, ast.Call))
    literal_tokens = Counter(
        _literal_token(n.value) for n in ast.walk(fn) if isinstance(n, ast.Constant)
    )
    external_names = Counter(
        n.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    )
    has_yield = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(fn))
    has_await = any(isinstance(n, ast.Await) for n in ast.walk(fn))
    control_count = sum(1 for n in ast.walk(fn) if isinstance(n, CONTROL_FLOW_NODES))
    returns = sum(1 for n in ast.walk(fn) if isinstance(n, ast.Return))
    raises = sum(1 for n in ast.walk(fn) if isinstance(n, ast.Raise))
    literals = sum(literal_tokens.values())
    side_effect_calls = sorted(
        {name for name in call_names if name.split(".")[-1] in SIDE_EFFECT_CALLS}
    )
    is_wrapper = False
    wrapper_target = None
    fixed_args = 0
    passthrough_args = 0

    body = getattr(fn, "body", [])
    if len(body) == 1:
        stmt = body[0]
        call = None
        if (isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Call)) or (
            isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
        ):
            call = stmt.value
        if call is not None:
            is_wrapper = True
            wrapper_target = _call_name(call)
            arg_names = {
                a.arg
                for a in list(fn.args.posonlyargs)
                + list(fn.args.args)
                + list(fn.args.kwonlyargs)
            }
            for arg in call.args:
                if isinstance(arg, ast.Name) and arg.id in arg_names:
                    passthrough_args += 1
                else:
                    fixed_args += 1

    returns_lambda = False
    curry_depth = 0
    for stmt in body:
        candidate = stmt.value if isinstance(stmt, ast.Return) else None
        while isinstance(candidate, ast.Lambda):
            returns_lambda = True
            curry_depth += 1
            candidate = candidate.body

    return {
        "node_types": node_types,
        "stmt_seq": stmt_seq,
        "call_names": call_names,
        "external_names": external_names,
        "param_count": param_count(fn),
        "has_yield": has_yield,
        "has_await": has_await,
        "control_count": control_count,
        "returns": returns,
        "raises": raises,
        "literals": literals,
        "literal_tokens": literal_tokens,
        "side_effect_calls": side_effect_calls,
        "is_wrapper": is_wrapper,
        "wrapper_target": wrapper_target,
        "fixed_args": fixed_args,
        "passthrough_args": passthrough_args,
        "returns_lambda": returns_lambda,
        "curry_depth": curry_depth,
        "stmt_count": len(stmt_seq),
    }


def occurrence_for(
    path: Path,
    fn: _FuncNode,
    parents: list[str],
    *,
    is_method_flag: bool | None = None,
) -> FunctionOccurrence:
    resolved_is_method = (
        is_method(parents) if is_method_flag is None else is_method_flag
    )
    return FunctionOccurrence(
        path=str(path),
        lineno=getattr(fn, "lineno", 0),
        end_lineno=getattr(fn, "end_lineno", None),
        col_offset=getattr(fn, "col_offset", 0),
        name=fn.name,
        qualname=build_qualname(parents, fn.name),
        kind="async def" if isinstance(fn, ast.AsyncFunctionDef) else "def",
        param_count=param_count(fn),
        is_method=resolved_is_method,
    )
