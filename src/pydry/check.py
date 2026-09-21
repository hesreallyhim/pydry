from __future__ import annotations

import json
import os
import shlex
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .engine import (
    block_clones,
    exact_groups,
    near_matches,
    scan_functions,
    suppress_covered_blocks,
    to_jsonable,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .config import CheckConfig
    from .models import (
        BlockCloneGroup,
        BlockOccurrence,
        ExactGroup,
        FunctionOccurrence,
        SimilarityResult,
    )

BASELINE_VERSION = 1


@dataclass(frozen=True)
class PolicyViolation:
    category: str
    actual: int
    allowed: int
    message: str


@dataclass(frozen=True)
class Baseline:
    """Fingerprints of findings that a repository has chosen to accept."""

    exact: frozenset[str]
    near: frozenset[str]
    blocks: frozenset[str]


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


# ── Baselines ────────────────────────────────────────────────


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


def _flagged(rows: list[Any], accepted: set[str], key: Any) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        data = to_jsonable(row)
        data["baselined"] = key(row) in accepted
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
    accepted_exact = set(baseline.exact) if baseline else set()
    accepted_near = set(baseline.near) if baseline else set()
    accepted_blocks = set(baseline.blocks) if baseline else set()
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
        "exact": _flagged(exact_rows, accepted_exact, lambda g: g.hash),
        "near": _flagged(near_rows[: config.top_k], accepted_near, near_fingerprint),
        "abstract": _flagged(
            abstract_rows[: config.top_k], accepted_near, near_fingerprint
        ),
        "blocks": _flagged(
            block_rows[: config.top_k], accepted_blocks, lambda g: g.hash
        ),
    }


# ── GitHub rendering ─────────────────────────────────────────


def _escape_command(value: object, *, property_value: bool = False) -> str:
    escaped = str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if property_value:
        escaped = escaped.replace(":", "%3A").replace(",", "%2C")
    return escaped


def _annotation(
    level: str,
    message: str,
    occurrence: FunctionOccurrence | BlockOccurrence | None = None,
    *,
    title: str = "pydry",
) -> None:
    properties = [f"title={_escape_command(title, property_value=True)}"]
    if occurrence is not None:
        properties.extend(
            [
                f"file={_escape_command(occurrence.path, property_value=True)}",
                f"line={occurrence.lineno}",
            ]
        )
        col_offset = getattr(occurrence, "col_offset", None)
        if col_offset is not None:
            properties.append(f"col={col_offset + 1}")
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
    block_rows: list[BlockCloneGroup] | None = None,
) -> int:
    failing = {violation.category for violation in violations}
    emitted = 0

    def emit(message: str, occurrence: FunctionOccurrence | BlockOccurrence) -> None:
        nonlocal emitted
        if emitted < config.annotation_limit:
            _annotation("error", message, occurrence)
            emitted += 1

    if "exact_groups" in failing:
        for group in exact_rows:
            names = ", ".join(item.qualname for item in group.occurrences)
            for occurrence in group.occurrences:
                emit(
                    f"Exact duplicate group ({group.count} occurrences, {group.tier}):"
                    f" {names}",
                    occurrence,
                )
    if "block_clones" in failing:
        for block in block_rows or []:
            places = ", ".join(
                f"{item.qualname} ({item.path}:{item.lineno})"
                for item in block.occurrences
            )
            for block_occurrence in block.occurrences:
                emit(
                    f"Repeated block of {block.stmt_count} statements"
                    f" ({block.count} occurrences): {places}",
                    block_occurrence,
                )
    if "near_matches" in failing:
        for row in near_rows:
            message = (
                f"Near match: {row.a.qualname} and {row.b.qualname} "
                f"(similarity {row.similarity_score:.3f},"
                f" {row.shared_statements} shared statements)"
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
        stream.write(f"block-clones={summary.get('block_clone_count', 0)}\n")


def _write_github_summary(
    *,
    root: Path,
    config: CheckConfig,
    summary: dict[str, int],
    new_counts: dict[str, int],
    violations: list[PolicyViolation],
    report: Path,
    config_path: Path | None,
    baseline_path: Path | None,
) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    status = "Passed" if not violations else "Failed"
    reproduce = ["pydry", "check", str(root)]
    if config_path is not None:
        reproduce.extend(["--config", str(config_path)])
    reproduce.extend(
        [
            "--profile",
            config.profile,
            "--threshold",
            str(config.threshold),
            "--top-k",
            str(config.top_k),
            "--min-statements",
            str(config.min_statements),
            "--block-min-statements",
            str(config.block_min_statements),
        ]
    )
    boolean_options = {
        "top-level-only": config.top_level_only,
        "strict": config.strict,
        "normalize-local-names": config.normalize_local_names,
        "normalize-constants": config.normalize_constants,
        "ignore-trivial": config.ignore_trivial,
        "fail-on-scan-errors": config.fail_on_scan_errors,
        "fail-on-plugin-errors": config.fail_on_plugin_errors,
    }
    reproduce.extend(
        f"--{name}" if enabled else f"--no-{name}"
        for name, enabled in boolean_options.items()
    )
    limits = {
        "max-exact-groups": config.max_exact_groups,
        "max-block-clones": config.max_block_clones,
        "max-near-matches": config.max_near_matches,
        "max-abstract-candidates": config.max_abstract_candidates,
    }
    for name, value in limits.items():
        reproduce.extend([f"--{name}", "none" if value is None else str(value)])
    for pattern in config.exclude:
        reproduce.extend(["--exclude", pattern])
    if baseline_path is not None:
        reproduce.extend(["--baseline", str(baseline_path)])
    reproduce.extend(["--annotation-limit", str(config.annotation_limit)])

    rows = [
        (
            "Exact duplicate groups",
            "exact_group_count",
            "exact",
            config.max_exact_groups,
        ),
        ("Repeated blocks", "block_clone_count", "blocks", config.max_block_clones),
        ("Near matches", "near_count", "near", config.max_near_matches),
        (
            "Abstraction candidates",
            "abstract_count",
            "abstract",
            config.max_abstract_candidates,
        ),
    ]
    lines = [
        f"## pydry check: {status}",
        "",
        "| Finding | Total | New | Allowed |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label, total_key, new_key, limit in rows:
        lines.append(
            f"| {label} | {summary[total_key]} | {new_counts[new_key]} |"
            f" {_display_limit(limit)} |"
        )
    lines.extend(
        [
            "",
            f"Configuration: profile `{config.profile}`,"
            f" threshold `{config.threshold}`,"
            f" minimum statements `{config.min_statements}`,"
            f" block size `{config.block_min_statements}`.",
            "",
            f"Report: `{report}`",
        ]
    )
    if baseline_path is not None:
        lines.extend(["", f"Baseline: `{baseline_path}` (accepted findings excluded)"])
    lines.extend(
        ["", "Reproduce locally:", "", f"```console\n{shlex.join(reproduce)}\n```"]
    )
    if violations:
        lines.extend(["", "### Policy violations", ""])
        lines.extend(f"- {item.message}" for item in violations)
    with Path(summary_path).open("a", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def _display_limit(value: int | None) -> str:
    return "not enforced" if value is None else str(value)


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
                profiles=profiles,
            ),
            near_rows,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if github:
            _annotation("error", str(exc), title="pydry analysis failed")
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
                _annotation("error", str(exc), title="pydry baseline failed")
            return 2
    elif baseline_path is not None:
        print(
            f"Warning: baseline {baseline_path} does not exist;"
            " evaluating all findings.",
            file=sys.stderr,
        )
        baseline_path = None

    if baseline is not None:
        new_exact = [g for g in exact_rows if g.hash not in baseline.exact]
        new_near = [r for r in near_rows if near_fingerprint(r) not in baseline.near]
        new_abstract = [
            r for r in abstract_rows if near_fingerprint(r) not in baseline.near
        ]
        new_blocks = [g for g in block_rows if g.hash not in baseline.blocks]
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
            _annotation("error", str(exc), title="pydry report failed")
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
        emitted = _finding_annotations(
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
            _annotation(level, message, title=f"pydry {kind} error")
        try:
            _write_github_outputs(typed_summary, not violations, output_path)
            _write_github_summary(
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
            _annotation("error", str(exc), title="pydry GitHub output failed")
            return 2
    return 0 if not violations else 1
