"""Baseline files: findings a repository has chosen to accept."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from .models import BlockCloneGroup, ExactGroup, SimilarityResult

BASELINE_VERSION = 2


@dataclass(frozen=True)
class Baseline:
    """Accepted findings, keyed by content.

    Exact groups and repeated blocks record the number of occurrences that
    were accepted, so adding another copy of an accepted duplicate is still
    reported. Near matches are pairs and are keyed by both sides' content.
    """

    exact: dict[str, int]
    near: frozenset[str]
    blocks: dict[str, int]

    def accepts_exact(self, group: ExactGroup) -> bool:
        return group.count <= self.exact.get(group.hash, 0)

    def accepts_block(self, group: BlockCloneGroup) -> bool:
        return group.count <= self.blocks.get(group.hash, 0)

    def accepts_near(self, row: SimilarityResult) -> bool:
        return near_fingerprint(row) in self.near


def near_fingerprint(row: SimilarityResult) -> str:
    return str(row.metadata.get("fingerprint", ""))


def load_baseline(path: Path) -> Baseline:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read baseline {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in baseline {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Unsupported baseline format in {path}")
    version = data.get("version")
    if version != BASELINE_VERSION:
        raise ValueError(
            f"Unsupported baseline version {version!r} in {path};"
            " regenerate it with --update-baseline"
        )

    def _counts(key: str) -> dict[str, int]:
        values = data.get(key, {})
        if not isinstance(values, dict) or not all(
            isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
            for k, v in values.items()
        ):
            raise ValueError(f"Baseline key {key!r} must map hashes to counts")
        return dict(values)

    near = data.get("near", [])
    if not isinstance(near, list) or not all(isinstance(v, str) for v in near):
        raise ValueError("Baseline key 'near' must be a list of strings")
    return Baseline(
        exact=_counts("exact"), near=frozenset(near), blocks=_counts("blocks")
    )


def write_baseline(
    path: Path,
    *,
    exact_rows: list[ExactGroup],
    near_rows: list[SimilarityResult],
    block_rows: list[BlockCloneGroup],
) -> None:
    payload = {
        "version": BASELINE_VERSION,
        "exact": {g.hash: g.count for g in sorted(exact_rows, key=lambda g: g.hash)},
        "near": sorted({near_fingerprint(r) for r in near_rows}),
        "blocks": {g.hash: g.count for g in sorted(block_rows, key=lambda g: g.hash)},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
