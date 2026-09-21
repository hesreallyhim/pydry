from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FunctionOccurrence:
    path: str
    lineno: int
    end_lineno: int | None
    col_offset: int
    name: str
    qualname: str
    kind: str
    param_count: int
    is_method: bool
    stmt_count: int = 0


@dataclass
class ExactGroup:
    """Functions whose whole bodies are equivalent under normalization.

    ``tier`` records the tightest equivalence that holds for every member:
    ``identical`` (only docstrings, annotations, decorators, and parameter
    names differ), ``renamed`` (local variables are renamed), or
    ``constants`` (literal values differ as well).
    """

    hash: str
    count: int
    occurrences: list[FunctionOccurrence]
    tier: str = "constants"
    stmt_count: int = 0
    savings: int = 0
    canonical: str | None = None


@dataclass
class SimilarityEvidence:
    """Statement alignment counts between two functions."""

    shared_statements: int
    a_statements: int
    b_statements: int
    identical_statements: int
    renamed_statements: int
    constant_statements: int
    only_in_a: int
    only_in_b: int
    call_similarity: float
    parameter_statements: int = 0


@dataclass
class SimilarityResult:
    similarity_score: float
    refactorability_score: float
    priority: float
    shared_statements: int
    pattern_labels: list[str]
    shared_structure_summary: str
    key_differences: list[str]
    risk_flags: list[str]
    suggested_refactor_kind: str
    a: FunctionOccurrence
    b: FunctionOccurrence
    evidence: SimilarityEvidence
    cluster_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NearCluster:
    """Transitive closure of near-match pairs."""

    cluster_id: int
    members: list[FunctionOccurrence]
    pair_count: int
    shared_statements: int
    savings: int
    priority: float
    suggested_refactor_kind: str


@dataclass(frozen=True)
class BlockOccurrence:
    path: str
    lineno: int
    end_lineno: int
    qualname: str
    start_index: int
    end_index: int


@dataclass
class BlockCloneGroup:
    """A run of consecutive statements repeated in two or more places."""

    hash: str
    stmt_count: int
    count: int
    occurrences: list[BlockOccurrence]
    savings: int
    summary: str
