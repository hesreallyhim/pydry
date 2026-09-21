from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from . import builtin_plugins  # noqa: F401
from .analyze import FunctionProfile, _counter_jaccard, canonicalize
from .blocks import DEFAULT_BLOCK_MIN_STATEMENTS, block_clones, suppress_covered_blocks
from .canonical import bag_upper_bound, lcs_alignment, sequence_similarity
from .models import (
    ExactGroup,
    FunctionOccurrence,
    NearCluster,
    SimilarityEvidence,
    SimilarityResult,
)
from .plugins import PairContext, PluginContext, apply_pair_plugins
from .scan import (
    DEFAULT_EXACT_OPTS,
    DEFAULT_MIN_STATEMENTS,
    DEFAULT_THRESHOLD,
    TIER_OPTS,
    resolve_profiles,
    scan_functions,
    sha,
    tier_hash,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from .canonical import StmtToken

_SortKey = tuple[float, float, str, int, str, int]

__all__ = [
    "DEFAULT_BLOCK_MIN_STATEMENTS",
    "DEFAULT_MIN_STATEMENTS",
    "DEFAULT_THRESHOLD",
    "abstract_candidates",
    "block_clones",
    "cluster_near_matches",
    "exact_groups",
    "near_matches",
    "scan_functions",
    "suppress_covered_blocks",
    "to_jsonable",
]


# ── Exact groups ─────────────────────────────────────────────


def exact_groups(
    root: Path,
    *,
    min_count: int = 2,
    top_level_only: bool = False,
    include_canonical: bool = False,
    normalize_local_names: bool = True,
    normalize_constants: bool = True,
    strict: bool = False,
    scan_errors: list[str] | None = None,
    min_statements: int = DEFAULT_MIN_STATEMENTS,
    ignore_trivial: bool = True,
    exclude: Sequence[str] = (),
    profiles: list[FunctionProfile] | None = None,
) -> list[ExactGroup]:
    """Group functions whose whole bodies are equivalent under normalization."""

    if min_count < 2:
        msg = "min_count must be >= 2"
        raise ValueError(msg)
    items = resolve_profiles(
        root,
        profiles,
        top_level_only=top_level_only,
        strict=strict,
        scan_errors=scan_errors,
        exclude=exclude,
    )
    if normalize_constants and not normalize_local_names:
        tier = "constants"
        opts = {
            **DEFAULT_EXACT_OPTS,
            "normalize_local_names": False,
            "normalize_constants": True,
        }
    elif normalize_constants:
        tier = "constants"
        opts = {**DEFAULT_EXACT_OPTS, **TIER_OPTS["constants"]}
    elif normalize_local_names:
        tier = "renamed"
        opts = {**DEFAULT_EXACT_OPTS, **TIER_OPTS["renamed"]}
    else:
        tier = "identical"
        opts = {**DEFAULT_EXACT_OPTS, **TIER_OPTS["identical"]}

    cacheable = opts == {**DEFAULT_EXACT_OPTS, **TIER_OPTS[tier]}
    groups: dict[str, list[FunctionProfile]] = defaultdict(list)
    canonical_by_hash: dict[str, str] = {}
    for profile in items:
        if not profile.eligible(
            min_statements=min_statements, ignore_trivial=ignore_trivial
        ):
            continue
        if cacheable and not include_canonical:
            h = tier_hash(profile, tier)
        else:
            canonical = canonicalize(profile.node, **opts)
            h = sha(canonical)
            if include_canonical and h not in canonical_by_hash:
                canonical_by_hash[h] = canonical
        groups[h].append(profile)

    res = []
    for h, members in groups.items():
        if len(members) < min_count:
            continue
        resolved_tier = _resolve_tier(members, tier)
        members.sort(
            key=lambda p: (
                p.occurrence.path,
                p.occurrence.lineno,
                p.occurrence.qualname,
            )
        )
        stmt_count = max(p.stmt_count for p in members)
        res.append(
            ExactGroup(
                hash=h,
                count=len(members),
                occurrences=[p.occurrence for p in members],
                tier=resolved_tier,
                stmt_count=stmt_count,
                savings=stmt_count * (len(members) - 1),
                canonical=canonical_by_hash.get(h),
            )
        )
    res.sort(key=lambda g: (-g.savings, -g.count, g.hash))
    return res


def _resolve_tier(members: list[FunctionProfile], requested: str) -> str:
    """Tightest tier at which every member still hashes the same."""

    for tier in ("identical", "renamed"):
        if requested == tier:
            return tier
        hashes = {tier_hash(p, tier) for p in members}
        if len(hashes) == 1:
            return tier
    return requested


# ── Near matches ─────────────────────────────────────────────


def _classify_alignment(
    a: list[StmtToken], b: list[StmtToken], pairs: list[tuple[int, int]]
) -> tuple[int, int, int, int]:
    identical = renamed = constant = parameter = 0
    for i, j in pairs:
        ta, tb = a[i], b[j]
        if ta.raw == tb.raw:
            identical += 1
        elif ta.names == tb.names:
            renamed += 1
        elif ta.full == tb.full:
            constant += 1
        else:
            parameter += 1
    return identical, renamed, constant, parameter


def _pattern_labels(
    evidence: SimilarityEvidence, af: dict[str, Any], bf: dict[str, Any]
) -> list[str]:
    labels = []
    inserted = evidence.only_in_a + evidence.only_in_b
    if (
        evidence.renamed_statements
        and not evidence.constant_statements
        and not inserted
    ):
        labels.append("renamed_locals")
    if evidence.constant_statements and not inserted:
        labels.append("literal_specialization")
    if evidence.parameter_statements:
        labels.append("parameter_specialization")
    if inserted:
        labels.append("structural_variant")
    if evidence.constant_statements and inserted:
        labels.append("mixed_variation")
    if af["call_names"] and bf["call_names"] and evidence.call_similarity < 0.5:
        labels.append("different_dependencies")
    return labels


def _difference_notes(
    evidence: SimilarityEvidence,
    a: FunctionOccurrence,
    b: FunctionOccurrence,
) -> list[str]:
    notes = []
    if evidence.only_in_a:
        notes.append(f"{evidence.only_in_a} statement(s) only in {a.qualname}")
    if evidence.only_in_b:
        notes.append(f"{evidence.only_in_b} statement(s) only in {b.qualname}")
    if evidence.constant_statements:
        notes.append(f"constants differ in {evidence.constant_statements} statement(s)")
    if evidence.parameter_statements:
        notes.append(
            f"a parameter on one side is a literal on the other in"
            f" {evidence.parameter_statements} statement(s)"
        )
    if evidence.renamed_statements:
        notes.append(
            f"local names differ in {evidence.renamed_statements} statement(s)"
        )
    if a.param_count != b.param_count:
        notes.append(f"parameter count differs ({a.param_count} vs {b.param_count})")
    return notes


def _shared_summary(
    a: FunctionProfile, b: FunctionProfile, pairs: list[tuple[int, int]]
) -> str:
    kinds = Counter(a.tokens[i].kind for i, _ in pairs)
    common_calls = sorted(set(a.features["call_names"]) & set(b.features["call_names"]))
    parts = []
    if kinds:
        described = ", ".join(
            f"{kind} x{count}" for kind, count in kinds.most_common(4)
        )
        parts.append(f"{len(pairs)} aligned statement(s): {described}")
    if common_calls:
        parts.append("shared calls: " + ", ".join(common_calls[:6]))
    return "; ".join(parts) if parts else "no aligned statements"


def _suggest_refactor(
    labels: list[str], risks: list[str], evidence: SimilarityEvidence
) -> str:
    if "async_boundary_diff" in risks or "return_shape_diff" in risks:
        return "leave_separate"
    if "renamed_locals" in labels:
        return "remove_duplicate"
    if "parameter_specialization" in labels and not (
        evidence.only_in_a or evidence.only_in_b
    ):
        return "delegate_to_general_form"
    if "literal_specialization" in labels:
        return "parameterize_constant"
    if "different_dependencies" in labels:
        return "inject_dependency"
    if "structural_variant" in labels:
        total = evidence.a_statements + evidence.b_statements
        divergence = (evidence.only_in_a + evidence.only_in_b) / max(total, 1)
        return "extract_common_helper" if divergence <= 0.35 else "extract_shared_steps"
    return "extract_common_helper"


def _refactorability(
    labels: list[str], evidence: SimilarityEvidence, similarity: float
) -> float:
    score = similarity
    if "renamed_locals" in labels or "literal_specialization" in labels:
        score += 0.05
    if "different_dependencies" in labels:
        score -= 0.10
    if "parameter_specialization" in labels:
        score -= 0.05
    return score


def _result_sort_key(result: SimilarityResult) -> _SortKey:
    return (
        -result.priority,
        -result.similarity_score,
        result.a.path,
        result.a.lineno,
        result.b.path,
        result.b.lineno,
    )


def _align(
    a: FunctionProfile, b: FunctionProfile, *, threshold: float
) -> list[tuple[int, int]] | None:
    """Align statements strictly, then loosely if strict alignment falls short.

    The loose form treats a parameter and a literal in the same position as
    equal, which catches a function that hard-codes an argument of a more
    general sibling. Returns ``None`` when neither alignment reaches
    ``threshold``.
    """

    la, lb = a.stmt_count, b.stmt_count
    strict: list[tuple[int, int]] = []
    if bag_upper_bound(a.key_counts, b.key_counts, la, lb) >= threshold:
        strict = lcs_alignment(a.keys, b.keys)
    strict_similarity = sequence_similarity(len(strict), la, lb)
    loose_bound = bag_upper_bound(a.loose_counts, b.loose_counts, la, lb)
    if loose_bound >= threshold and loose_bound > strict_similarity:
        loose = lcs_alignment(a.loose_keys, b.loose_keys)
        if len(loose) > len(strict):
            strict = loose
    if strict and sequence_similarity(len(strict), la, lb) >= threshold:
        return strict
    return None


def _compare(
    a: FunctionProfile,
    b: FunctionProfile,
    *,
    threshold: float,
    plugin_errors: list[str] | None,
) -> SimilarityResult | None:
    pairs = _align(a, b, threshold=threshold)
    if pairs is None:
        return None
    shared = len(pairs)
    similarity = sequence_similarity(shared, a.stmt_count, b.stmt_count)

    af, bf = a.features, b.features
    identical, renamed, constant, parameter = _classify_alignment(
        a.tokens, b.tokens, pairs
    )
    evidence = SimilarityEvidence(
        shared_statements=shared,
        a_statements=a.stmt_count,
        b_statements=b.stmt_count,
        identical_statements=identical,
        renamed_statements=renamed,
        constant_statements=constant,
        only_in_a=a.stmt_count - shared,
        only_in_b=b.stmt_count - shared,
        call_similarity=round(_counter_jaccard(af["call_names"], bf["call_names"]), 4),
        parameter_statements=parameter,
    )
    base_labels = _pattern_labels(evidence, af, bf)
    base_diffs = _difference_notes(evidence, a.occurrence, b.occurrence)

    plugin_result = apply_pair_plugins(
        PairContext(
            a=PluginContext(occurrence=a.occurrence, node=a.node, features=af),
            b=PluginContext(occurrence=b.occurrence, node=b.node, features=bf),
            evidence=evidence,
        ),
        plugin_errors=plugin_errors,
    )
    labels = list(dict.fromkeys([*base_labels, *plugin_result.pattern_labels]))
    risks = list(dict.fromkeys(plugin_result.risk_flags))
    diffs = list(dict.fromkeys([*base_diffs, *plugin_result.key_differences]))

    refactorability = _refactorability(labels, evidence, similarity)
    refactorability += plugin_result.refactorability_delta
    refactorability = max(0.0, min(1.0, refactorability))
    suggested = plugin_result.suggested_refactor_kind or _suggest_refactor(
        labels, risks, evidence
    )
    if suggested != "leave_separate" and (
        "async_boundary_diff" in risks or "return_shape_diff" in risks
    ):
        suggested = "leave_separate"
    priority = shared * refactorability

    return SimilarityResult(
        similarity_score=round(similarity, 4),
        refactorability_score=round(refactorability, 4),
        priority=round(priority, 2),
        shared_statements=shared,
        pattern_labels=labels,
        shared_structure_summary=_shared_summary(a, b, pairs),
        key_differences=diffs,
        risk_flags=risks,
        suggested_refactor_kind=suggested,
        a=a.occurrence,
        b=b.occurrence,
        evidence=evidence,
        metadata={"fingerprint": pair_fingerprint(a, b), **plugin_result.metadata},
    )


def pair_fingerprint(a: FunctionProfile, b: FunctionProfile) -> str:
    """Order-independent content identity of a pair, used by baselines."""

    return "|".join(sorted((tier_hash(a, "constants"), tier_hash(b, "constants"))))


def near_matches(
    root: Path,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    top_k: int | None = None,
    top_level_only: bool = False,
    strict: bool = False,
    scan_errors: list[str] | None = None,
    plugin_errors: list[str] | None = None,
    min_statements: int = DEFAULT_MIN_STATEMENTS,
    ignore_trivial: bool = True,
    exclude: Sequence[str] = (),
    profiles: list[FunctionProfile] | None = None,
) -> list[SimilarityResult]:
    """Rank pairs of functions by how much aligned structure they share.

    Functions that are exact duplicates of one another (at the loosest tier)
    are represented by a single member, so exact groups are not repeated here.
    Results are sorted by ``priority`` (shared statements weighted by the
    refactorability estimate) and carry a ``cluster_id`` linking transitively
    connected pairs.
    """

    if not 0.0 <= threshold <= 1.0:
        msg = "threshold must be between 0 and 1"
        raise ValueError(msg)
    if top_k is not None and top_k < 0:
        msg = "top_k must be >= 0"
        raise ValueError(msg)

    items = resolve_profiles(
        root,
        profiles,
        top_level_only=top_level_only,
        strict=strict,
        scan_errors=scan_errors,
        exclude=exclude,
    )
    if top_k == 0:
        return []

    representatives: dict[str, FunctionProfile] = {}
    for profile in sorted(
        items, key=lambda p: (p.occurrence.path, p.occurrence.lineno)
    ):
        if not profile.eligible(
            min_statements=min_statements, ignore_trivial=ignore_trivial
        ):
            continue
        representatives.setdefault(tier_hash(profile, "constants"), profile)
    candidates = sorted(representatives.values(), key=lambda p: p.stmt_count)

    out: list[SimilarityResult] = []
    for a, b in _candidate_pairs(candidates, threshold):
        result = _compare(a, b, threshold=threshold, plugin_errors=plugin_errors)
        if result is not None:
            out.append(result)

    out.sort(key=_result_sort_key)
    _assign_clusters(out)
    if top_k is not None:
        out = out[:top_k]
    return out


def _candidate_pairs(
    candidates: list[FunctionProfile], threshold: float
) -> Iterator[tuple[FunctionProfile, FunctionProfile]]:
    """Yield the pairs that could reach ``threshold``, shorter side first.

    ``candidates`` must be sorted by statement count. Two functions can only
    reach similarity ``t`` when the longer is at most ``(2 - t) / t`` times
    the shorter, and when their loose token multisets overlap in at least
    ``s = t * len / (2 - t)`` elements. With tokens ordered by global rarity,
    any two multisets overlapping in ``s`` elements share an element within
    their first ``len - s + 1`` tokens (prefix filtering), so indexing only
    those prefixes finds every qualifying pair without comparing all of them.
    Loose tokens are used because loose overlap is never smaller than strict.
    """

    if threshold <= 0.0:
        for i, a in enumerate(candidates):
            for b in candidates[i + 1 :]:
                yield a, b
        return

    max_ratio = (2.0 - threshold) / threshold
    frequency: Counter[tuple[int, str]] = Counter()
    for profile in candidates:
        frequency.update(profile.loose_counts.keys())

    def prefix(profile: FunctionProfile) -> list[tuple[int, tuple[int, str], int]]:
        length = profile.stmt_count
        needed = math.ceil(threshold * length / (2.0 - threshold))
        expanded = sorted(
            (frequency[key], key, index)
            for key, count in profile.loose_counts.items()
            for index in range(count)
        )
        return expanded[: max(1, length - needed + 1)]

    index: dict[tuple[int, tuple[int, str], int], list[int]] = defaultdict(list)
    for position, a in enumerate(candidates):
        tokens = prefix(a)
        seen: set[int] = set()
        for token in tokens:
            for other in index.get(token, ()):
                if other in seen:
                    continue
                seen.add(other)
                b = candidates[other]
                if a.stmt_count <= b.stmt_count * max_ratio:
                    yield b, a
        for token in tokens:
            index[token].append(position)


def _occurrence_key(occ: FunctionOccurrence) -> tuple[str, int, str]:
    return (occ.path, occ.lineno, occ.qualname)


def _assign_clusters(results: list[SimilarityResult]) -> None:
    parent: dict[tuple[str, int, str], tuple[str, int, str]] = {}

    def find(key: tuple[str, int, str]) -> tuple[str, int, str]:
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for result in results:
        ra = find(_occurrence_key(result.a))
        rb = find(_occurrence_key(result.b))
        if ra != rb:
            parent[rb] = ra

    ids: dict[tuple[str, int, str], int] = {}
    for result in results:
        root = find(_occurrence_key(result.a))
        result.cluster_id = ids.setdefault(root, len(ids) + 1)


def cluster_near_matches(results: list[SimilarityResult]) -> list[NearCluster]:
    """Summarize near-match pairs as clusters of connected functions."""

    by_cluster: dict[int, list[SimilarityResult]] = defaultdict(list)
    for result in results:
        by_cluster[result.cluster_id or 0].append(result)
    clusters = []
    for cluster_id, rows in by_cluster.items():
        members: dict[tuple[str, int, str], FunctionOccurrence] = {}
        for row in rows:
            members.setdefault(_occurrence_key(row.a), row.a)
            members.setdefault(_occurrence_key(row.b), row.b)
        ordered = sorted(members.values(), key=_occurrence_key)
        shared = min(row.shared_statements for row in rows)
        suggestions = Counter(row.suggested_refactor_kind for row in rows)
        clusters.append(
            NearCluster(
                cluster_id=cluster_id,
                members=ordered,
                pair_count=len(rows),
                shared_statements=shared,
                savings=shared * (len(ordered) - 1),
                priority=max(row.priority for row in rows),
                suggested_refactor_kind=suggestions.most_common(1)[0][0],
            )
        )
    clusters.sort(key=lambda c: (-c.priority, -c.savings, c.cluster_id))
    return clusters


def abstract_candidates(
    root: Path,
    *,
    threshold: float = 0.82,
    top_k: int | None = None,
    top_level_only: bool = False,
    strict: bool = False,
    scan_errors: list[str] | None = None,
    plugin_errors: list[str] | None = None,
    min_statements: int = DEFAULT_MIN_STATEMENTS,
    ignore_trivial: bool = True,
    exclude: Sequence[str] = (),
    profiles: list[FunctionProfile] | None = None,
) -> list[SimilarityResult]:
    matches = near_matches(
        root,
        threshold=threshold,
        top_k=None,
        top_level_only=top_level_only,
        strict=strict,
        scan_errors=scan_errors,
        plugin_errors=plugin_errors,
        min_statements=min_statements,
        ignore_trivial=ignore_trivial,
        exclude=exclude,
        profiles=profiles,
    )
    rows = [m for m in matches if m.suggested_refactor_kind != "leave_separate"]
    if top_k is not None:
        rows = rows[:top_k]
    return rows


# ── Serialization ────────────────────────────────────────────


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, list):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(to_jsonable(x) for x in obj)
    if hasattr(obj, "__dataclass_fields__"):
        data = asdict(obj)
        return to_jsonable(data)
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    return obj
