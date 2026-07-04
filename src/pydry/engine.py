from __future__ import annotations

import ast
import hashlib
from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from . import builtin_plugins  # noqa: F401
from .analyze import (
    _counter_jaccard,
    _lcs_ratio,
    canonicalize,
    extract_features,
    iter_functions,
    iter_python_files,
    occurrence_for,
)
from .models import ExactGroup, FunctionOccurrence, SimilarityEvidence, SimilarityResult
from .plugins import PairContext, PluginContext, apply_pair_plugins

if TYPE_CHECKING:
    from pathlib import Path

_FeatureDict = dict[str, Any]
_SortKey = tuple[float, float, str, int, str, int]

DEFAULT_EXACT_OPTS = dict(
    strip_docstrings=True,
    strip_decorators=True,
    normalize_arg_names=True,
    strip_annotations=True,
    normalize_local_names=False,
    normalize_constants=False,
    preserve_function_name=False,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scan_functions(
    root: Path,
    *,
    top_level_only: bool = False,
    strict: bool = False,
    scan_errors: list[str] | None = None,
) -> list[dict[str, Any]]:
    out = []
    for path in iter_python_files(root):
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
                {
                    "occurrence": occurrence_for(
                        path, fn, parents, is_method_flag=is_method_flag
                    ),
                    "node": fn,
                    "features": extract_features(fn),
                }
            )
    return out


def exact_groups(
    root: Path,
    *,
    min_count: int = 2,
    top_level_only: bool = False,
    include_canonical: bool = False,
    normalize_local_names: bool = False,
    normalize_constants: bool = False,
    strict: bool = False,
    scan_errors: list[str] | None = None,
) -> list[ExactGroup]:
    if min_count < 2:
        msg = "min_count must be >= 2"
        raise ValueError(msg)
    groups = defaultdict(list)
    canonical_by_hash = {}
    opts = dict(DEFAULT_EXACT_OPTS)
    opts["normalize_local_names"] = normalize_local_names
    opts["normalize_constants"] = normalize_constants
    for item in scan_functions(
        root,
        top_level_only=top_level_only,
        strict=strict,
        scan_errors=scan_errors,
    ):
        canonical = canonicalize(item["node"], **opts)
        h = _sha(canonical)
        groups[h].append(item["occurrence"])
        if include_canonical and h not in canonical_by_hash:
            canonical_by_hash[h] = canonical
    res = []
    for h, occs in groups.items():
        if len(occs) >= min_count:
            res.append(
                ExactGroup(
                    hash=h,
                    count=len(occs),
                    occurrences=sorted(
                        occs, key=lambda o: (o.path, o.lineno, o.qualname)
                    ),
                    canonical=canonical_by_hash.get(h),
                )
            )
    res.sort(key=lambda g: (-g.count, g.hash))
    return res


def _sig_similarity(a: _FeatureDict, b: _FeatureDict) -> float:
    a_pc: int = a["param_count"]
    b_pc: int = b["param_count"]
    pc = 1.0 - (abs(a_pc - b_pc) / max(a_pc, b_pc, 1))
    modality = 1.0
    if a["has_yield"] != b["has_yield"]:
        modality -= 0.35
    if a["has_await"] != b["has_await"]:
        modality -= 0.25
    return max(0.0, min(1.0, 0.7 * pc + 0.3 * modality))


def _wrapper_score(a: _FeatureDict, b: _FeatureDict) -> float:
    score = 0.0
    if a["is_wrapper"] and b["is_wrapper"]:
        score += 0.5
        if (
            a["wrapper_target"] == b["wrapper_target"]
            and a["wrapper_target"] is not None
        ):
            score += 0.35
    elif a["is_wrapper"] or b["is_wrapper"]:
        score += 0.25
    return min(score, 1.0)


def _curry_score(a: _FeatureDict, b: _FeatureDict) -> float:
    if a["returns_lambda"] and b["returns_lambda"]:
        return 0.8 if a["curry_depth"] == b["curry_depth"] else 0.6
    if a["returns_lambda"] or b["returns_lambda"]:
        return 0.4
    return 0.0


def _shape_similarity(a: _FeatureDict, b: _FeatureDict) -> float:
    return _counter_jaccard(a["node_types"], b["node_types"])


def _stmt_similarity(a: _FeatureDict, b: _FeatureDict) -> float:
    return _lcs_ratio(a["stmt_seq"], b["stmt_seq"])


def _call_similarity(a: _FeatureDict, b: _FeatureDict) -> float:
    return _counter_jaccard(a["call_names"], b["call_names"])


def _difference_notes(a: _FeatureDict, b: _FeatureDict) -> list[str]:
    notes = []
    if a["param_count"] != b["param_count"]:
        notes.append(
            f"parameter count differs ({a['param_count']} vs {b['param_count']})"
        )
    if a["has_await"] != b["has_await"]:
        notes.append("async behavior differs")
    if a["has_yield"] != b["has_yield"]:
        notes.append("generator behavior differs")
    if a["raises"] != b["raises"]:
        notes.append("exception behavior differs")
    if a["wrapper_target"] != b["wrapper_target"] and (
        a["is_wrapper"] or b["is_wrapper"]
    ):
        notes.append("wrapper targets differ")
    if abs(a["literals"] - b["literals"]) >= 2:
        notes.append("literal density differs")
    if abs(a["control_count"] - b["control_count"]) >= 2:
        notes.append("control-flow complexity differs")
    return notes


def _risk_flags(a: _FeatureDict, b: _FeatureDict) -> list[str]:
    flags = set()
    if a["side_effect_calls"] or b["side_effect_calls"]:
        flags.add("possible_side_effects")
    if a["has_await"] != b["has_await"]:
        flags.add("async_boundary_diff")
    if a["has_yield"] != b["has_yield"]:
        flags.add("return_shape_diff")
    if a["raises"] != b["raises"]:
        flags.add("exception_behavior_diff")
    ext_diff = len(set(a["external_names"]) ^ set(b["external_names"]))
    if ext_diff >= 6:
        flags.add("ambient_dependency_diff")
    return sorted(flags)


def _pattern_labels(
    a: _FeatureDict, b: _FeatureDict, evidence: SimilarityEvidence
) -> list[str]:
    literal_token_diff = _literal_token_diff(a, b)
    labels = []
    if evidence.wrapper_score >= 0.5:
        labels.append("wrapper")
    if evidence.curry_score >= 0.4:
        labels.append("partial_application")
    if evidence.shape_similarity >= 0.9 and evidence.call_similarity >= 0.85:
        labels.append("renamed_locals")
    if (
        literal_token_diff > 0
        and abs(a["literals"] - b["literals"]) <= 2
        and evidence.shape_similarity >= 0.85
        and evidence.call_similarity >= 0.6
    ):
        labels.append("literal_specialization")
    if evidence.shape_similarity >= 0.8 and evidence.stmt_similarity >= 0.8:
        labels.append("extract_helper_candidate")
    if (
        evidence.signature_similarity >= 0.8
        and abs(a["param_count"] - b["param_count"]) <= 1
        and evidence.call_similarity < 0.5
    ):
        labels.append("same_shape_different_dependencies")
    return labels


def _literal_token_diff(a: _FeatureDict, b: _FeatureDict) -> int:
    a_tokens: dict[str, int] = a.get("literal_tokens", {})
    b_tokens: dict[str, int] = b.get("literal_tokens", {})
    keys = set(a_tokens) | set(b_tokens)
    return sum(abs(a_tokens.get(k, 0) - b_tokens.get(k, 0)) for k in keys)


def _shared_summary(a: _FeatureDict, b: _FeatureDict) -> str:
    common_calls = sorted(set(a["call_names"]) & set(b["call_names"]))
    common_stmt = sorted(set(a["stmt_seq"]) & set(b["stmt_seq"]))
    parts = []
    if common_stmt:
        parts.append("shared statements: " + ", ".join(common_stmt[:6]))
    if common_calls:
        parts.append("shared calls: " + ", ".join(common_calls[:6]))
    if not parts:
        parts.append("shared AST shape without strong call overlap")
    return "; ".join(parts)


def _suggest_refactor(
    labels: list[str], risks: list[str], evidence: SimilarityEvidence
) -> str:
    if "wrapper" in labels and evidence.wrapper_score >= 0.5:
        return "merge_into_single_function_with_param"
    if "partial_application" in labels:
        return "introduce_partial"
    if "extract_helper_candidate" in labels and "possible_side_effects" not in risks:
        return "extract_common_helper"
    if "literal_specialization" in labels:
        return "parameterize_constant"
    if (
        "ambient_dependency_diff" in risks
        or "async_boundary_diff" in risks
        or "return_shape_diff" in risks
    ):
        return "leave_separate"
    return "move_to_utils"


def _refactorability(
    labels: list[str], risks: list[str], evidence: SimilarityEvidence
) -> float:
    score = (
        0.35 * evidence.shape_similarity
        + 0.15 * evidence.stmt_similarity
        + 0.10 * evidence.call_similarity
        + 0.10 * evidence.signature_similarity
        + 0.20 * evidence.wrapper_score
        + 0.10 * evidence.curry_score
    )
    if "extract_helper_candidate" in labels:
        score += 0.08
    if "literal_specialization" in labels:
        score += 0.05
    score -= 0.1 * len(risks)
    return max(0.0, min(1.0, score))


def _abstract_template(
    a_occ: FunctionOccurrence,
    b_occ: FunctionOccurrence,
    labels: list[str],
    shared_summary: str,
) -> str | None:
    if (
        "extract_helper_candidate" in labels
        or "literal_specialization" in labels
        or "wrapper" in labels
    ):
        return (
            f"def shared_helper(...):\n"
            f"    # candidate abstraction for {a_occ.qualname} and {b_occ.qualname}\n"
            f"    # {shared_summary}\n"
            f"    ..."
        )
    return None


def _result_sort_key(result: SimilarityResult) -> _SortKey:
    return (
        -result.refactorability_score,
        -result.similarity_score,
        result.a.path,
        result.a.lineno,
        result.b.path,
        result.b.lineno,
    )


def near_matches(
    root: Path,
    *,
    threshold: float = 0.8,
    top_k: int | None = None,
    top_level_only: bool = False,
    strict: bool = False,
    scan_errors: list[str] | None = None,
    plugin_errors: list[str] | None = None,
) -> list[SimilarityResult]:
    if not 0.0 <= threshold <= 1.0:
        msg = "threshold must be between 0 and 1"
        raise ValueError(msg)
    if top_k is not None and top_k < 0:
        msg = "top_k must be >= 0"
        raise ValueError(msg)

    items = scan_functions(
        root,
        top_level_only=top_level_only,
        strict=strict,
        scan_errors=scan_errors,
    )
    bounded_top_k = top_k
    if bounded_top_k == 0:
        return []

    out: list[SimilarityResult] = []
    top_rows: list[SimilarityResult] = []
    top_keys: list[_SortKey] = []
    for i in range(len(items)):
        a = items[i]
        af = a["features"]
        for j in range(i + 1, len(items)):
            b = items[j]
            bf = b["features"]
            size_ratio = min(af["stmt_count"], bf["stmt_count"]) / max(
                af["stmt_count"], bf["stmt_count"], 1
            )
            if size_ratio < 0.4:
                continue
            shape_similarity = _shape_similarity(af, bf)
            call_similarity = _call_similarity(af, bf)
            signature_similarity = _sig_similarity(af, bf)
            wrapper_score = _wrapper_score(af, bf)
            curry_score = _curry_score(af, bf)

            similarity_upper_bound = (
                0.40 * shape_similarity
                + 0.20
                + 0.15 * call_similarity
                + 0.10 * signature_similarity
                + 0.10 * wrapper_score
                + 0.05 * curry_score
            )
            if similarity_upper_bound < threshold:
                continue
            stmt_similarity = _stmt_similarity(af, bf)
            evidence = SimilarityEvidence(
                shape_similarity=shape_similarity,
                stmt_similarity=stmt_similarity,
                call_similarity=call_similarity,
                signature_similarity=signature_similarity,
                wrapper_score=wrapper_score,
                curry_score=curry_score,
            )
            similarity = (
                0.40 * shape_similarity
                + 0.20 * stmt_similarity
                + 0.15 * call_similarity
                + 0.10 * signature_similarity
                + 0.10 * wrapper_score
                + 0.05 * curry_score
            )
            if similarity < threshold:
                continue
            base_risks = _risk_flags(af, bf)
            base_labels = _pattern_labels(af, bf, evidence)
            summary = _shared_summary(af, bf)
            base_diffs = _difference_notes(af, bf)

            plugin_result = apply_pair_plugins(
                PairContext(
                    a=PluginContext(
                        occurrence=a["occurrence"], node=a["node"], features=af
                    ),
                    b=PluginContext(
                        occurrence=b["occurrence"], node=b["node"], features=bf
                    ),
                    evidence=evidence,
                ),
                plugin_errors=plugin_errors,
            )

            risks = []
            for item in [*base_risks, *plugin_result.risk_flags]:
                if item not in risks:
                    risks.append(item)

            labels = []
            for item in [*base_labels, *plugin_result.pattern_labels]:
                if item not in labels:
                    labels.append(item)

            diffs = []
            for item in [*base_diffs, *plugin_result.key_differences]:
                if item not in diffs:
                    diffs.append(item)

            refactorability = (
                _refactorability(labels, risks, evidence)
                + plugin_result.refactorability_delta
            )
            refactorability = max(0.0, min(1.0, refactorability))
            suggested = plugin_result.suggested_refactor_kind or _suggest_refactor(
                labels, risks, evidence
            )
            abstract_template = plugin_result.abstract_template or _abstract_template(
                a["occurrence"], b["occurrence"], labels, summary
            )
            metadata = {"size_ratio": round(size_ratio, 4), **plugin_result.metadata}

            result = SimilarityResult(
                similarity_score=round(similarity, 4),
                refactorability_score=round(refactorability, 4),
                pattern_labels=labels,
                shared_structure_summary=summary,
                key_differences=diffs,
                risk_flags=risks,
                suggested_refactor_kind=suggested,
                a=a["occurrence"],
                b=b["occurrence"],
                evidence=evidence,
                abstract_template=abstract_template,
                metadata=metadata,
            )
            if bounded_top_k is None:
                out.append(result)
            else:
                key = _result_sort_key(result)
                if len(top_rows) < bounded_top_k:
                    idx = bisect_right(top_keys, key)
                    top_keys.insert(idx, key)
                    top_rows.insert(idx, result)
                elif key < top_keys[-1]:
                    idx = bisect_right(top_keys, key)
                    top_keys.insert(idx, key)
                    top_rows.insert(idx, result)
                    top_keys.pop()
                    top_rows.pop()

    if bounded_top_k is None:
        out.sort(key=_result_sort_key)
        if top_k is not None:
            out = out[:top_k]
        return out
    return top_rows


def abstract_candidates(
    root: Path,
    *,
    threshold: float = 0.82,
    top_k: int | None = None,
    top_level_only: bool = False,
    strict: bool = False,
    scan_errors: list[str] | None = None,
    plugin_errors: list[str] | None = None,
) -> list[SimilarityResult]:
    matches = near_matches(
        root,
        threshold=threshold,
        top_k=top_k,
        top_level_only=top_level_only,
        strict=strict,
        scan_errors=scan_errors,
        plugin_errors=plugin_errors,
    )
    return [m for m in matches if m.suggested_refactor_kind != "leave_separate"]


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, list):
        return [to_jsonable(x) for x in obj]
    if hasattr(obj, "__dataclass_fields__"):
        data = asdict(obj)
        return to_jsonable(data)
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    return obj
