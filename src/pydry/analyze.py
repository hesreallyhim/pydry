from __future__ import annotations

import ast
import builtins
import fnmatch
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .canonical import StmtToken, bound_names, is_trivial, statement_tokens
from .models import FunctionOccurrence
from .normalize import FunctionNormalizer

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Sequence

_FuncNode = ast.FunctionDef | ast.AsyncFunctionDef

SIDE_EFFECT_CALLS = {
    "print",
    "write",
    "writelines",
    "send",
    "sendall",
    "post",
    "put",
    "patch",
    "delete",
    "remove",
    "unlink",
    "rmtree",
    "save",
    "commit",
    "execute",
    "mkdir",
    "makedirs",
    "rename",
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

_BUILTIN_NAMES = frozenset(dir(builtins))


def _excluded(relative: str, patterns: Sequence[str]) -> bool:
    return any(
        fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(relative, f"{pattern}/*")
        for pattern in patterns
    )


def iter_python_files(root: Path, exclude: Sequence[str] = ()) -> Iterable[Path]:
    """Yield Python files under ``root`` in a stable order.

    ``exclude`` holds glob patterns matched against paths relative to ``root``
    using forward slashes; a pattern matching a directory excludes its
    contents.
    """

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        kept = []
        for name in sorted(dirnames):
            if name in DEFAULT_EXCLUDED_DIRS:
                continue
            relative = Path(dirpath, name).relative_to(root).as_posix()
            if exclude and _excluded(relative, exclude):
                continue
            kept.append(name)
        dirnames[:] = kept
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            path = Path(dirpath, filename)
            if exclude and _excluded(path.relative_to(root).as_posix(), exclude):
                continue
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


def _counter_jaccard(a: Counter[str], b: Counter[str]) -> float:
    keys = set(a) | set(b)
    if not keys:
        return 1.0
    inter = sum(min(a[k], b[k]) for k in keys)
    union = sum(max(a[k], b[k]) for k in keys)
    return inter / union if union else 1.0


def extract_features(fn: _FuncNode) -> dict[str, Any]:
    call_names = Counter(_call_name(n) for n in ast.walk(fn) if isinstance(n, ast.Call))
    local = bound_names(fn)
    external_names = frozenset(
        n.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Name)
        and isinstance(n.ctx, ast.Load)
        and n.id not in local
        and n.id not in _BUILTIN_NAMES
    )
    has_yield = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(fn))
    has_await = any(isinstance(n, ast.Await) for n in ast.walk(fn))
    control_count = sum(1 for n in ast.walk(fn) if isinstance(n, CONTROL_FLOW_NODES))
    returns = sum(1 for n in ast.walk(fn) if isinstance(n, ast.Return))
    raises = sum(1 for n in ast.walk(fn) if isinstance(n, ast.Raise))
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
        "call_names": call_names,
        "external_names": external_names,
        "param_count": param_count(fn),
        "has_yield": has_yield,
        "has_await": has_await,
        "control_count": control_count,
        "returns": returns,
        "raises": raises,
        "side_effect_calls": side_effect_calls,
        "is_wrapper": is_wrapper,
        "wrapper_target": wrapper_target,
        "fixed_args": fixed_args,
        "passthrough_args": passthrough_args,
        "returns_lambda": returns_lambda,
        "curry_depth": curry_depth,
    }


def occurrence_for(
    path: Path,
    fn: _FuncNode,
    parents: list[str],
    *,
    is_method_flag: bool | None = None,
    stmt_count: int = 0,
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
        stmt_count=stmt_count,
    )


@dataclass
class FunctionProfile:
    """Everything the engine needs to know about one function."""

    occurrence: FunctionOccurrence
    node: _FuncNode
    tokens: list[StmtToken]
    features: dict[str, Any]
    trivial: bool

    def __post_init__(self) -> None:
        self.keys: list[tuple[int, str]] = [token.key for token in self.tokens]
        self.key_counts: dict[tuple[int, str], int] = dict(Counter(self.keys))
        self.loose_keys: list[tuple[int, str]] = [t.loose_key for t in self.tokens]
        self.loose_counts: dict[tuple[int, str], int] = dict(Counter(self.loose_keys))
        self._hashes: dict[str, str] = {}

    @property
    def stmt_count(self) -> int:
        return len(self.tokens)

    def eligible(self, *, min_statements: int, ignore_trivial: bool) -> bool:
        if self.stmt_count < min_statements:
            return False
        return not (ignore_trivial and self.trivial)


def profile_function(
    path: Path, fn: _FuncNode, parents: list[str], *, is_method_flag: bool
) -> FunctionProfile:
    tokens = statement_tokens(fn)
    return FunctionProfile(
        occurrence=occurrence_for(
            path, fn, parents, is_method_flag=is_method_flag, stmt_count=len(tokens)
        ),
        node=fn,
        tokens=tokens,
        features=extract_features(fn),
        trivial=is_trivial(tokens),
    )
