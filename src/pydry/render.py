"""Plain-text rendering of findings for the terminal."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from .engine import cluster_near_matches

if TYPE_CHECKING:
    from collections.abc import Callable

    from .models import (
        BlockCloneGroup,
        ExactGroup,
        FunctionOccurrence,
        SimilarityResult,
    )


def print_warning(header: str, messages: list[str], *, sample: int = 5) -> None:
    print(header, file=sys.stderr)
    for msg in messages[:sample]:
        print(f"  - {msg}", file=sys.stderr)
    if len(messages) > sample:
        print(f"  ... {len(messages) - sample} more", file=sys.stderr)


def print_diagnostics(scan_errors: list[str], plugin_errors: list[str]) -> None:
    if scan_errors:
        print_warning(
            f"Warning: skipped {len(scan_errors)} file(s) due to parse/read errors."
            " Use --strict to fail instead.",
            scan_errors,
        )
    if plugin_errors:
        unique_errors = list(dict.fromkeys(plugin_errors))
        print_warning(
            f"Warning: {len(unique_errors)} plugin error(s) occurred."
            " Plugin failures were isolated.",
            unique_errors,
        )


def location(path: str, lineno: int, end_lineno: int | None) -> str:
    end = f"-{end_lineno}" if end_lineno else ""
    return f"{path}:{lineno}{end}"


def function_line(occ: FunctionOccurrence) -> str:
    where = location(occ.path, occ.lineno, occ.end_lineno)
    return f"  {where}  {occ.kind} {occ.qualname}"


def print_exact(groups: list[ExactGroup]) -> None:
    if not groups:
        print("No duplicate functions found.")
        return
    for i, g in enumerate(groups, start=1):
        print(
            f"Group {i}: {g.count} occurrences, {g.tier}, {g.stmt_count} statements"
            f" each, saves ~{g.savings} statements  hash={g.hash[:12]}"
        )
        for occ in g.occurrences:
            print(function_line(occ))
        print()


def print_pair(r: SimilarityResult, indent: str = "  ") -> None:
    print(
        f"{indent}- {r.a.qualname} <-> {r.b.qualname}:"
        f" similarity {r.similarity_score:.2f},"
        f" {r.shared_statements} shared of {r.evidence.a_statements}"
        f" and {r.evidence.b_statements} statements, confidence"
        f" {r.refactorability_score:.2f}"
    )
    print(f"{indent}  suggestion: {r.suggested_refactor_kind}")
    if r.pattern_labels:
        print(f"{indent}  labels: " + ", ".join(r.pattern_labels))
    if r.risk_flags:
        print(f"{indent}  risks: " + ", ".join(r.risk_flags))
    if r.key_differences:
        print(f"{indent}  differences: " + "; ".join(r.key_differences))


def print_near(rows: list[SimilarityResult], *, pairs_per_cluster: int = 3) -> None:
    if not rows:
        print("No near matches found.")
        return
    clusters = cluster_near_matches(rows)
    by_cluster: dict[int | None, list[SimilarityResult]] = {}
    for row in rows:
        by_cluster.setdefault(row.cluster_id, []).append(row)
    for i, cluster in enumerate(clusters, start=1):
        print(
            f"Cluster {i}: {len(cluster.members)} functions,"
            f" {cluster.shared_statements} shared statements,"
            f" saves ~{cluster.savings} statements ({cluster.suggested_refactor_kind})"
        )
        for occ in cluster.members:
            print(function_line(occ))
        pairs = by_cluster.get(cluster.cluster_id, [])
        for row in pairs[:pairs_per_cluster]:
            print_pair(row)
        if len(pairs) > pairs_per_cluster:
            print(
                f"  ... {len(pairs) - pairs_per_cluster} more pair(s) in this cluster"
            )
        print()


def print_blocks(groups: list[BlockCloneGroup]) -> None:
    if not groups:
        print("No repeated blocks found.")
        return
    for i, g in enumerate(groups, start=1):
        print(
            f"Block {i}: {g.stmt_count} statements repeated {g.count} times,"
            f" saves ~{g.savings} statements ({g.summary})"
        )
        for occ in g.occurrences:
            where = location(occ.path, occ.lineno, occ.end_lineno)
            print(f"  {where}  in {occ.qualname}")
        print()


def print_section(
    title: str, rows: list[dict[str, Any]], render: Callable[[dict[str, Any]], str]
) -> None:
    print(f"\n{title}")
    if not rows:
        print("  none")
        return
    for i, row in enumerate(rows, start=1):
        print(f"  {i}. {render(row)}")


def print_showcase(payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    top = payload["top_examples"]
    settings = payload["settings"]

    print("=" * 72)
    print("PYDRY SHOWCASE")
    print("=" * 72)
    print(f"Corpus: {payload['root']}")
    print(
        "Summary: "
        f"exact_groups={summary['exact_group_count']} "
        f"repeated_blocks={summary['block_clone_count']} "
        f"near_pairs={summary['near_count']} "
        f"abstract_candidates={summary['abstract_count']}"
    )
    print(f"Config: threshold={settings['threshold']} top_k={settings['top_k']}")

    def exact_line(group: dict[str, Any]) -> str:
        names = ", ".join(group["qualnames"])
        return (
            f"{group['count']}x {group['tier']}, {group['stmt_count']}"
            f" statements, saves ~{group['savings']}: {names}"
        )

    def block_line(block: dict[str, Any]) -> str:
        where = ", ".join(block["locations"])
        return (
            f"{block['stmt_count']} statements x{block['count']},"
            f" saves ~{block['savings']}: {where}"
        )

    def near_line(cluster: dict[str, Any]) -> str:
        names = ", ".join(cluster["members"])
        return (
            f"priority {cluster['priority']:.1f},"
            f" {cluster['shared_statements']} shared statements,"
            f" saves ~{cluster['savings']}"
            f" ({cluster['suggested_refactor_kind']}): {names}"
        )

    print_section("[1/3] Exact duplicates (whole functions)", top["exact"], exact_line)
    print_section(
        "[2/3] Repeated blocks (inside larger functions)", top["blocks"], block_line
    )
    print_section("[3/3] Near matches (ranked by priority)", top["near"], near_line)

    print("\nTip: rerun with --format json for machine-readable snapshots.")
