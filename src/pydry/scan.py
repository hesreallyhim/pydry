"""Scanning and per-function canonical hashes shared by every analysis."""

from __future__ import annotations

import ast
import hashlib
from typing import TYPE_CHECKING

from .analyze import (
    FunctionProfile,
    canonicalize,
    iter_functions,
    iter_python_files,
    profile_function,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

DEFAULT_THRESHOLD = 0.8


DEFAULT_MIN_STATEMENTS = 2


DEFAULT_EXACT_OPTS = dict(
    strip_docstrings=True,
    strip_decorators=True,
    normalize_arg_names=True,
    strip_annotations=True,
    normalize_local_names=False,
    normalize_constants=False,
    preserve_function_name=False,
)


TIER_OPTS: dict[str, dict[str, bool]] = {
    "identical": {"normalize_local_names": False, "normalize_constants": False},
    "renamed": {"normalize_local_names": True, "normalize_constants": False},
    "constants": {"normalize_local_names": True, "normalize_constants": True},
}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tier_hash(profile: FunctionProfile, tier: str) -> str:
    cached = profile._hashes.get(tier)
    if cached is None:
        opts = {**DEFAULT_EXACT_OPTS, **TIER_OPTS[tier]}
        cached = sha(canonicalize(profile.node, **opts))
        profile._hashes[tier] = cached
    return cached


def scan_functions(
    root: Path,
    *,
    top_level_only: bool = False,
    strict: bool = False,
    scan_errors: list[str] | None = None,
    exclude: Sequence[str] = (),
) -> list[FunctionProfile]:
    """Parse every Python file under ``root`` and profile each function."""

    out: list[FunctionProfile] = []
    for path in iter_python_files(root, exclude=exclude):
        try:
            module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception as exc:
            msg = f"{path}: {type(exc).__name__}: {exc}"
            if strict:
                strict_msg = f"Failed to parse/read {path}: {type(exc).__name__}: {exc}"
                raise RuntimeError(strict_msg) from exc
            if scan_errors is not None:
                scan_errors.append(msg)
            continue
        for fn, parents, is_method_flag in iter_functions(
            module, top_level_only=top_level_only
        ):
            out.append(
                profile_function(path, fn, parents, is_method_flag=is_method_flag)
            )
    return out


def resolve_profiles(
    root: Path,
    profiles: list[FunctionProfile] | None,
    *,
    top_level_only: bool,
    strict: bool,
    scan_errors: list[str] | None,
    exclude: Sequence[str],
) -> list[FunctionProfile]:
    if profiles is not None:
        return profiles
    return scan_functions(
        root,
        top_level_only=top_level_only,
        strict=strict,
        scan_errors=scan_errors,
        exclude=exclude,
    )
