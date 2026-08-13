from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .engine import exact_groups, near_matches, to_jsonable

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .config import CheckConfig
    from .models import ExactGroup, FunctionOccurrence, SimilarityResult


@dataclass(frozen=True)
class PolicyViolation:
    category: str
    actual: int
    allowed: int
    message: str


def _dedupe(messages: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(messages))


def _violation(
    category: str, actual: int, allowed: int | None, label: str
) -> PolicyViolation | None:
    if allowed is None or actual <= allowed:
        return None
    return PolicyViolation(
        category=category,
        actual=actual,
        allowed=allowed,
        message=f"Found {actual} {label}; policy allows {allowed}.",
    )


def evaluate_policy(
    *,
    config: CheckConfig,
    exact_count: int,
    near_count: int,
    abstract_count: int,
    scan_errors: list[str],
    plugin_errors: list[str],
) -> list[PolicyViolation]:
    violations = [
        _violation(
            "exact_groups",
            exact_count,
            config.max_exact_groups,
            "exact duplicate group(s)",
        ),
        _violation(
            "near_matches",
            near_count,
            config.max_near_matches,
            "near match(es)",
        ),
        _violation(
            "abstract_candidates",
            abstract_count,
            config.max_abstract_candidates,
            "abstraction candidate(s)",
        ),
    ]
    resolved = [item for item in violations if item is not None]
    if config.fail_on_scan_errors and scan_errors:
        resolved.append(
            PolicyViolation(
                category="scan_errors",
                actual=len(scan_errors),
                allowed=0,
                message=f"Skipped {len(scan_errors)} file(s) due to parse/read errors.",
            )
        )
    if config.fail_on_plugin_errors and plugin_errors:
        resolved.append(
            PolicyViolation(
                category="plugin_errors",
                actual=len(plugin_errors),
                allowed=0,
                message=f"Encountered {len(plugin_errors)} plugin error(s).",
            )
        )
    return resolved


def _diagnostics(scan_errors: list[str], plugin_errors: list[str]) -> dict[str, object]:
    return {
        "scan_errors_count": len(scan_errors),
        "scan_error_samples": scan_errors[:5],
        "plugin_errors_count": len(plugin_errors),
        "plugin_error_samples": plugin_errors[:5],
    }


def _limits(config: CheckConfig) -> dict[str, int | None]:
    return {
        "max_exact_groups": config.max_exact_groups,
        "max_near_matches": config.max_near_matches,
        "max_abstract_candidates": config.max_abstract_candidates,
    }


def _report_payload(
    *,
    root: Path,
    config: CheckConfig,
    exact_rows: list[ExactGroup],
    near_rows: list[SimilarityResult],
    abstract_rows: list[SimilarityResult],
    violations: list[PolicyViolation],
) -> dict[str, Any]:
    return {
        "root": str(root),
        "settings": {
            "threshold": config.threshold,
            "top_k": config.top_k,
            "top_level_only": config.top_level_only,
            "strict": config.strict,
            "exact_normalization": {
                "normalize_local_names": config.normalize_local_names,
                "normalize_constants": config.normalize_constants,
            },
        },
        "summary": {
            "exact_group_count": len(exact_rows),
            "near_count": len(near_rows),
            "abstract_count": len(abstract_rows),
        },
        "check": {
            "passed": not violations,
            "limits": _limits(config),
            "violations": [asdict(item) for item in violations],
            "report_truncated": {
                "near": len(near_rows) > config.top_k,
                "abstract": len(abstract_rows) > config.top_k,
            },
        },
        "exact": to_jsonable(exact_rows),
        "near": to_jsonable(near_rows[: config.top_k]),
        "abstract": to_jsonable(abstract_rows[: config.top_k]),
    }


def _escape_command(value: object, *, property_value: bool = False) -> str:
    escaped = str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if property_value:
        escaped = escaped.replace(":", "%3A").replace(",", "%2C")
    return escaped


def _annotation(
    level: str,
    message: str,
    occurrence: FunctionOccurrence | None = None,
    *,
    title: str = "pydry",
) -> None:
    properties = [f"title={_escape_command(title, property_value=True)}"]
    if occurrence is not None:
        properties.extend(
            [
                f"file={_escape_command(occurrence.path, property_value=True)}",
                f"line={occurrence.lineno}",
                f"col={occurrence.col_offset + 1}",
            ]
        )
        if occurrence.end_lineno is not None:
            properties.append(f"endLine={occurrence.end_lineno}")
    print(f"::{level} {','.join(properties)}::{_escape_command(message)}")


def _finding_annotations(
    *,
    config: CheckConfig,
    violations: list[PolicyViolation],
    exact_rows: list[ExactGroup],
    near_rows: list[SimilarityResult],
    abstract_rows: list[SimilarityResult],
) -> int:
    failing = {violation.category for violation in violations}
    emitted = 0

    def emit(message: str, occurrence: FunctionOccurrence) -> None:
        nonlocal emitted
        if emitted < config.annotation_limit:
            _annotation("error", message, occurrence)
            emitted += 1

    if "exact_groups" in failing:
        for group in exact_rows:
            names = ", ".join(item.qualname for item in group.occurrences)
            for occurrence in group.occurrences:
                emit(
                    f"Exact duplicate group ({group.count} occurrences): {names}",
                    occurrence,
                )
    if "near_matches" in failing:
        for row in near_rows:
            message = (
                f"Near match: {row.a.qualname} and {row.b.qualname} "
                f"(similarity {row.similarity_score:.3f})"
            )
            emit(message, row.a)
            emit(message, row.b)
    if "abstract_candidates" in failing:
        for row in abstract_rows:
            message = (
                f"Abstraction candidate: {row.a.qualname} and {row.b.qualname}; "
                f"suggestion: {row.suggested_refactor_kind}"
            )
            emit(message, row.a)
            emit(message, row.b)

    return emitted


def _write_github_outputs(summary: dict[str, int], passed: bool, report: Path) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as stream:
        stream.write(f"result={'pass' if passed else 'fail'}\n")
        stream.write(f"report={report}\n")
        stream.write(f"exact-groups={summary['exact_group_count']}\n")
        stream.write(f"near-matches={summary['near_count']}\n")
        stream.write(f"abstract-candidates={summary['abstract_count']}\n")


def _write_github_summary(
    *,
    root: Path,
    config: CheckConfig,
    summary: dict[str, int],
    violations: list[PolicyViolation],
    report: Path,
) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    status = "Passed" if not violations else "Failed"
    exact_limit = _display_limit(config.max_exact_groups)
    near_limit = _display_limit(config.max_near_matches)
    abstract_limit = _display_limit(config.max_abstract_candidates)
    lines = [
        f"## pydry check: {status}",
        "",
        "| Finding | Count | Allowed |",
        "| --- | ---: | ---: |",
        f"| Exact duplicate groups | {summary['exact_group_count']} | {exact_limit} |",
        f"| Near matches | {summary['near_count']} | {near_limit} |",
        (
            "| Abstraction candidates | "
            f"{summary['abstract_count']} | {abstract_limit} |"
        ),
        "",
        f"Configuration: threshold `{config.threshold}`, top-k `{config.top_k}`.",
        "",
        f"Report: `{report}`",
        "",
        "Reproduce locally:",
        "",
        f"```console\npydry check {root}\n```",
    ]
    if violations:
        lines.extend(["", "### Policy violations", ""])
        lines.extend(f"- {item.message}" for item in violations)
    with Path(summary_path).open("a", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def _display_limit(value: int | None) -> str:
    return "not enforced" if value is None else str(value)


def run_check(
    *,
    root: Path,
    config: CheckConfig,
    output_path: Path,
    github: bool,
) -> int:
    """Run analysis, persist a report, render CI feedback, and return 0/1/2."""

    if not root.exists() or not root.is_dir():
        print(f"Invalid directory: {root}", file=sys.stderr)
        return 2
    if "\n" in str(output_path) or "\r" in str(output_path):
        print("Invalid output path: line breaks are not allowed.", file=sys.stderr)
        return 2

    exact_scan_errors: list[str] = []
    near_scan_errors: list[str] = []
    plugin_errors: list[str] = []
    try:
        exact_rows = exact_groups(
            root,
            min_count=2,
            top_level_only=config.top_level_only,
            normalize_local_names=config.normalize_local_names,
            normalize_constants=config.normalize_constants,
            strict=config.strict,
            scan_errors=exact_scan_errors,
        )
        near_rows = near_matches(
            root,
            threshold=config.threshold,
            top_k=None,
            top_level_only=config.top_level_only,
            strict=config.strict,
            scan_errors=near_scan_errors,
            plugin_errors=plugin_errors,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if github:
            _annotation("error", str(exc), title="pydry analysis failed")
        return 2

    scan_errors = _dedupe([*exact_scan_errors, *near_scan_errors])
    plugin_errors = _dedupe(plugin_errors)
    abstract_rows = [
        row for row in near_rows if row.suggested_refactor_kind != "leave_separate"
    ]
    violations = evaluate_policy(
        config=config,
        exact_count=len(exact_rows),
        near_count=len(near_rows),
        abstract_count=len(abstract_rows),
        scan_errors=scan_errors,
        plugin_errors=plugin_errors,
    )
    payload = _report_payload(
        root=root,
        config=config,
        exact_rows=exact_rows,
        near_rows=near_rows,
        abstract_rows=abstract_rows,
        violations=violations,
    )
    envelope = {
        "results": payload,
        "diagnostics": _diagnostics(scan_errors, plugin_errors),
    }
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"Error: Could not write report {output_path}: {exc}", file=sys.stderr)
        if github:
            _annotation("error", str(exc), title="pydry report failed")
        return 2

    summary = payload["summary"]
    assert isinstance(summary, dict)
    typed_summary = {str(key): int(value) for key, value in summary.items()}
    print(
        "pydry check: "
        f"{'passed' if not violations else 'failed'} "
        f"(exact={len(exact_rows)}, near={len(near_rows)}, "
        f"abstract={len(abstract_rows)})"
    )
    print(f"Report: {output_path}")
    for violation in violations:
        print(f"- {violation.message}", file=sys.stderr)

    if github:
        emitted = _finding_annotations(
            config=config,
            violations=violations,
            exact_rows=exact_rows,
            near_rows=near_rows,
            abstract_rows=abstract_rows,
        )
        failing_categories = {item.category for item in violations}
        diagnostics = [
            (
                "error" if "scan_errors" in failing_categories else "warning",
                "scan",
                message,
            )
            for message in scan_errors
        ]
        diagnostics.extend(
            (
                "error" if "plugin_errors" in failing_categories else "warning",
                "plugin",
                message,
            )
            for message in plugin_errors
        )
        remaining = max(0, config.annotation_limit - emitted)
        for level, kind, message in diagnostics[:remaining]:
            _annotation(level, message, title=f"pydry {kind} error")
        try:
            _write_github_outputs(typed_summary, not violations, output_path)
            _write_github_summary(
                root=root,
                config=config,
                summary=typed_summary,
                violations=violations,
                report=output_path,
            )
        except OSError as exc:
            print(f"Error: Could not write GitHub metadata: {exc}", file=sys.stderr)
            _annotation("error", str(exc), title="pydry GitHub output failed")
            return 2
    return 0 if not violations else 1
