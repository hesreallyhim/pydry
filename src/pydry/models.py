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


@dataclass
class ExactGroup:
    hash: str
    count: int
    occurrences: list[FunctionOccurrence]
    canonical: str | None = None


@dataclass
class SimilarityEvidence:
    shape_similarity: float
    stmt_similarity: float
    call_similarity: float
    signature_similarity: float
    wrapper_score: float
    curry_score: float


@dataclass
class SimilarityResult:
    similarity_score: float
    refactorability_score: float
    pattern_labels: list[str]
    shared_structure_summary: str
    key_differences: list[str]
    risk_flags: list[str]
    suggested_refactor_kind: str
    a: FunctionOccurrence
    b: FunctionOccurrence
    evidence: SimilarityEvidence
    abstract_template: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
