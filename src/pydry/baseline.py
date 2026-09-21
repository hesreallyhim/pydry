"""Baseline files: findings a repository has chosen to accept."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from .models import BlockCloneGroup, ExactGroup, SimilarityResult

BASELINE_VERSION = 1


@dataclass(frozen=True)
class Baseline:
    """Fingerprints of findings that a repository has chosen to accept."""

    exact: frozenset[str]
    near: frozenset[str]
    blocks: frozenset[str]


def near_fingerprint(row: SimilarityResult) -> str:
    return str(row.metadata.get("fingerprint", ""))


def load_baseline(path: Path) -> Baseline:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read baseline {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in baseline {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != BASELINE_VERSION:
        raise ValueError(f"Unsupported baseline format in {path}")

    def _set(key: str) -> frozenset[str]:
        values = data.get(key, [])
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError(f"Baseline key {key!r} must be a list of strings")
        return frozenset(values)

    return Baseline(exact=_set("exact"), near=_set("near"), blocks=_set("blocks"))


def write_baseline(
    path: Path,
    *,
    exact_rows: list[ExactGroup],
    near_rows: list[SimilarityResult],
    block_rows: list[BlockCloneGroup],
) -> None:
    payload = {
        "version": BASELINE_VERSION,
        "exact": sorted({g.hash for g in exact_rows}),
        "near": sorted({near_fingerprint(r) for r in near_rows}),
        "blocks": sorted({g.hash for g in block_rows}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
