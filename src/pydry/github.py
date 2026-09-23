"""GitHub Actions output: workflow annotations, step outputs, and job summary."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .check import PolicyViolation
    from .config import CheckConfig
    from .models import (
        BlockCloneGroup,
        BlockOccurrence,
        ExactGroup,
        FunctionOccurrence,
        SimilarityResult,
    )


def escape_command(value: object, *, property_value: bool = False) -> str:
    escaped = str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if property_value:
        escaped = escaped.replace(":", "%3A").replace(",", "%2C")
    return escaped


def annotation(
    level: str,
    message: str,
    occurrence: FunctionOccurrence | BlockOccurrence | None = None,
    *,
    title: str = "pydry",
) -> None:
    properties = [f"title={escape_command(title, property_value=True)}"]
    if occurrence is not None:
        properties.extend(
            [
                f"file={escape_command(occurrence.path, property_value=True)}",
                f"line={occurrence.lineno}",
            ]
        )
        col_offset = getattr(occurrence, "col_offset", None)
        if col_offset is not None:
            properties.append(f"col={col_offset + 1}")
        if occurrence.end_lineno is not None:
            properties.append(f"endLine={occurrence.end_lineno}")
    print(f"::{level} {','.join(properties)}::{escape_command(message)}")


def finding_annotations(
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
            annotation("error", message, occurrence)
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


def write_github_outputs(summary: dict[str, int], passed: bool, report: Path) -> None:
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


def write_github_summary(
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
            f" {display_limit(limit)} |"
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


def display_limit(value: int | None) -> str:
    return "not enforced" if value is None else str(value)
