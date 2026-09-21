from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .baseline import Baseline, load_baseline, write_baseline
from .engine import (
    block_clones,
    exact_groups,
    near_matches,
    scan_functions,
    suppress_covered_blocks,
    to_jsonable,
)
from .github import (
    annotation,
    finding_annotations,
    write_github_outputs,
    write_github_summary,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from .config import CheckConfig
    from .models import (
        BlockCloneGroup,
        ExactGroup,
        SimilarityResult,
    )


@dataclass(frozen=True)
class PolicyViolation:
    category: str
    actual: int
    allowed: int
    message: str


def _dedupe(messages: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(messages))


def _violation(
    category: str, actual: int, allowed: int | None, noun: str
) -> PolicyViolation | None:
    if allowed is None or actual <= allowed:
        return None
    return PolicyViolation(
        category=category,
        actual=actual,
        allowed=allowed,
        message=f"Found {actual} {noun}; policy allows {allowed}.",
    )


def evaluate_policy(
    *,
    config: CheckConfig,
    exact_count: int,
    near_count: int,
    abstract_count: int,
    block_count: int = 0,
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
            "block_clones",
            block_count,
            config.max_block_clones,
            "repeated block(s)",
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


# ── Report payload ───────────────────────────────────────────


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
        "max_block_clones": config.max_block_clones,
        "max_near_matches": config.max_near_matches,
        "max_abstract_candidates": config.max_abstract_candidates,
    }


def _flagged(rows: list[Any], accepted: Callable[[Any], bool]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        data = to_jsonable(row)
        data["baselined"] = accepted(row)
        out.append(data)
    return out


def _report_payload(
    *,
    root: Path,
    config: CheckConfig,
    exact_rows: list[ExactGroup],
    near_rows: list[SimilarityResult],
    abstract_rows: list[SimilarityResult],
    block_rows: list[BlockCloneGroup],
    violations: list[PolicyViolation],
    config_path: Path | None,
    baseline: Baseline | None,
    baseline_path: Path | None,
    new_counts: dict[str, int],
) -> dict[str, Any]:
    nothing: Callable[[Any], bool] = lambda row: False  # noqa: E731
    accepted_exact = baseline.accepts_exact if baseline else nothing
    accepted_near = baseline.accepts_near if baseline else nothing
    accepted_blocks = baseline.accepts_block if baseline else nothing
    return {
        "root": str(root),
        "config": str(config_path) if config_path is not None else None,
        "settings": {
            "profile": config.profile,
            "threshold": config.threshold,
            "top_k": config.top_k,
            "top_level_only": config.top_level_only,
            "strict": config.strict,
            "min_statements": config.min_statements,
            "ignore_trivial": config.ignore_trivial,
            "block_min_statements": config.block_min_statements,
            "exclude": list(config.exclude),
            "exact_normalization": {
                "normalize_local_names": config.normalize_local_names,
                "normalize_constants": config.normalize_constants,
            },
        },
        "summary": {
            "exact_group_count": len(exact_rows),
            "near_count": len(near_rows),
            "abstract_count": len(abstract_rows),
            "block_clone_count": len(block_rows),
        },
        "baseline": {
            "path": str(baseline_path) if baseline_path is not None else None,
            "applied": baseline is not None,
            "new": new_counts,
        },
        "check": {
            "passed": not violations,
            "limits": _limits(config),
            "violations": [asdict(item) for item in violations],
            "report_truncated": {
                "near": len(near_rows) > config.top_k,
                "abstract": len(abstract_rows) > config.top_k,
                "blocks": len(block_rows) > config.top_k,
            },
        },
        "exact": _flagged(exact_rows, accepted_exact),
        "near": _flagged(near_rows[: config.top_k], accepted_near),
        "abstract": _flagged(abstract_rows[: config.top_k], accepted_near),
        "blocks": _flagged(block_rows[: config.top_k], accepted_blocks),
    }


# ── Entry point ──────────────────────────────────────────────


def run_check(
    *,
    root: Path,
    config: CheckConfig,
    output_path: Path,
    github: bool,
    config_path: Path | None = None,
    baseline_path: Path | None = None,
    update_baseline: bool = False,
) -> int:
    """Run analysis, persist a report, render CI feedback, and return 0/1/2."""

    if not root.exists() or not root.is_dir():
        print(f"Invalid directory: {root}", file=sys.stderr)
        return 2
    if "\n" in str(output_path) or "\r" in str(output_path):
        print("Invalid output path: line breaks are not allowed.", file=sys.stderr)
        return 2

    scan_errors: list[str] = []
    plugin_errors: list[str] = []
    try:
        profiles = scan_functions(
            root,
            top_level_only=config.top_level_only,
            strict=config.strict,
            scan_errors=scan_errors,
            exclude=config.exclude,
        )
        exact_rows = exact_groups(
            root,
            min_count=2,
            normalize_local_names=config.normalize_local_names,
            normalize_constants=config.normalize_constants,
            min_statements=config.min_statements,
            ignore_trivial=config.ignore_trivial,
            profiles=profiles,
        )
        near_rows = near_matches(
            root,
            threshold=config.threshold,
            top_k=None,
            plugin_errors=plugin_errors,
            min_statements=config.min_statements,
            ignore_trivial=config.ignore_trivial,
            profiles=profiles,
        )
        block_rows = suppress_covered_blocks(
            block_clones(
                root,
                min_statements=config.block_min_statements,
                near_threshold=config.threshold,
                profiles=profiles,
            ),
            near_rows,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if github:
            annotation("error", str(exc), title="pydry analysis failed")
        return 2

    scan_errors = _dedupe(scan_errors)
    plugin_errors = _dedupe(plugin_errors)
    abstract_rows = [
        row for row in near_rows if row.suggested_refactor_kind != "leave_separate"
    ]

    baseline: Baseline | None = None
    if update_baseline:
        target = baseline_path or Path(".pydry-baseline.json")
        try:
            write_baseline(
                target,
                exact_rows=exact_rows,
                near_rows=near_rows,
                block_rows=block_rows,
            )
        except OSError as exc:
            print(f"Error: Could not write baseline {target}: {exc}", file=sys.stderr)
            return 2
        print(f"Baseline written to {target}")
        baseline_path = target
    if baseline_path is not None and baseline_path.is_file():
        try:
            baseline = load_baseline(baseline_path)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            if github:
                annotation("error", str(exc), title="pydry baseline failed")
            return 2
    elif baseline_path is not None:
        print(
            f"Warning: baseline {baseline_path} does not exist;"
            " evaluating all findings.",
            file=sys.stderr,
        )
        baseline_path = None

    if baseline is not None:
        new_exact = [g for g in exact_rows if not baseline.accepts_exact(g)]
        new_near = [r for r in near_rows if not baseline.accepts_near(r)]
        new_abstract = [r for r in abstract_rows if not baseline.accepts_near(r)]
        new_blocks = [g for g in block_rows if not baseline.accepts_block(g)]
    else:
        new_exact, new_near, new_abstract, new_blocks = (
            exact_rows,
            near_rows,
            abstract_rows,
            block_rows,
        )
    new_counts = {
        "exact": len(new_exact),
        "near": len(new_near),
        "abstract": len(new_abstract),
        "blocks": len(new_blocks),
    }

    violations = evaluate_policy(
        config=config,
        exact_count=len(new_exact),
        near_count=len(new_near),
        abstract_count=len(new_abstract),
        block_count=len(new_blocks),
        scan_errors=scan_errors,
        plugin_errors=plugin_errors,
    )
    payload = _report_payload(
        root=root,
        config=config,
        exact_rows=exact_rows,
        near_rows=near_rows,
        abstract_rows=abstract_rows,
        block_rows=block_rows,
        violations=violations,
        config_path=config_path,
        baseline=baseline,
        baseline_path=baseline_path,
        new_counts=new_counts,
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
            annotation("error", str(exc), title="pydry report failed")
        return 2

    summary = payload["summary"]
    assert isinstance(summary, dict)
    typed_summary = {str(key): int(value) for key, value in summary.items()}
    suffix = ""
    if baseline is not None:
        suffix = (
            f"; new: exact={new_counts['exact']}, blocks={new_counts['blocks']},"
            f" near={new_counts['near']}, abstract={new_counts['abstract']}"
        )
    print(
        "pydry check: "
        f"{'passed' if not violations else 'failed'} "
        f"(exact={len(exact_rows)}, blocks={len(block_rows)}, near={len(near_rows)}, "
        f"abstract={len(abstract_rows)}{suffix})"
    )
    print(f"Report: {output_path}")
    for violation in violations:
        print(f"- {violation.message}", file=sys.stderr)

    if github:
        emitted = finding_annotations(
            config=config,
            violations=violations,
            exact_rows=new_exact,
            near_rows=new_near,
            abstract_rows=new_abstract,
            block_rows=new_blocks,
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
            annotation(level, message, title=f"pydry {kind} error")
        try:
            write_github_outputs(typed_summary, not violations, output_path)
            write_github_summary(
                root=root,
                config=config,
                summary=typed_summary,
                new_counts=new_counts,
                violations=violations,
                report=output_path,
                config_path=config_path,
                baseline_path=baseline_path,
            )
        except OSError as exc:
            print(f"Error: Could not write GitHub metadata: {exc}", file=sys.stderr)
            annotation("error", str(exc), title="pydry GitHub output failed")
            return 2
    return 0 if not violations else 1
