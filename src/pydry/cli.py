from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .check import run_check
from .config import (
    CONFIG_FILENAME,
    PROFILES,
    ConfigError,
    apply_overrides,
    load_check_config,
)
from .engine import (
    DEFAULT_BLOCK_MIN_STATEMENTS,
    DEFAULT_MIN_STATEMENTS,
    DEFAULT_THRESHOLD,
    block_clones,
    cluster_near_matches,
    exact_groups,
    near_matches,
    scan_functions,
    suppress_covered_blocks,
    to_jsonable,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from .models import (
        BlockCloneGroup,
        ExactGroup,
        FunctionOccurrence,
        SimilarityResult,
    )


# ── Output helpers ───────────────────────────────────────────


def _diagnostics_payload(
    scan_errors: list[str], plugin_errors: list[str]
) -> dict[str, object]:
    unique_plugin_errors = list(dict.fromkeys(plugin_errors))
    return {
        "scan_errors_count": len(scan_errors),
        "scan_error_samples": scan_errors[:5],
        "plugin_errors_count": len(unique_plugin_errors),
        "plugin_error_samples": unique_plugin_errors[:5],
    }


def _emit_json_output(
    payload: object,
    *,
    scan_errors: list[str],
    plugin_errors: list[str],
    output_path: str | None,
) -> None:
    envelope = {
        "results": payload,
        "diagnostics": _diagnostics_payload(scan_errors, plugin_errors),
    }
    rendered = json.dumps(envelope, indent=2)
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote JSON report to {out}", file=sys.stderr)
        return
    print(rendered)


def _print_warning(header: str, messages: list[str], *, sample: int = 5) -> None:
    print(header, file=sys.stderr)
    for msg in messages[:sample]:
        print(f"  - {msg}", file=sys.stderr)
    if len(messages) > sample:
        print(f"  ... {len(messages) - sample} more", file=sys.stderr)


def _print_diagnostics(scan_errors: list[str], plugin_errors: list[str]) -> None:
    if scan_errors:
        _print_warning(
            f"Warning: skipped {len(scan_errors)} file(s) due to parse/read errors."
            " Use --strict to fail instead.",
            scan_errors,
        )
    if plugin_errors:
        unique_errors = list(dict.fromkeys(plugin_errors))
        _print_warning(
            f"Warning: {len(unique_errors)} plugin error(s) occurred."
            " Plugin failures were isolated.",
            unique_errors,
        )


def _location(path: str, lineno: int, end_lineno: int | None) -> str:
    end = f"-{end_lineno}" if end_lineno else ""
    return f"{path}:{lineno}{end}"


def _function_line(occ: FunctionOccurrence) -> str:
    where = _location(occ.path, occ.lineno, occ.end_lineno)
    return f"  {where}  {occ.kind} {occ.qualname}"


def _print_exact(groups: list[ExactGroup]) -> None:
    if not groups:
        print("No duplicate functions found.")
        return
    for i, g in enumerate(groups, start=1):
        print(
            f"Group {i}: {g.count} occurrences, {g.tier}, {g.stmt_count} statements"
            f" each, saves ~{g.savings} statements  hash={g.hash[:12]}"
        )
        for occ in g.occurrences:
            print(_function_line(occ))
        print()


def _print_pair(r: SimilarityResult, indent: str = "  ") -> None:
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


def _print_near(rows: list[SimilarityResult], *, pairs_per_cluster: int = 3) -> None:
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
            print(_function_line(occ))
        pairs = by_cluster.get(cluster.cluster_id, [])
        for row in pairs[:pairs_per_cluster]:
            _print_pair(row)
        if len(pairs) > pairs_per_cluster:
            print(
                f"  ... {len(pairs) - pairs_per_cluster} more pair(s) in this cluster"
            )
        print()


def _print_blocks(groups: list[BlockCloneGroup]) -> None:
    if not groups:
        print("No repeated blocks found.")
        return
    for i, g in enumerate(groups, start=1):
        print(
            f"Block {i}: {g.stmt_count} statements repeated {g.count} times,"
            f" saves ~{g.savings} statements ({g.summary})"
        )
        for occ in g.occurrences:
            where = _location(occ.path, occ.lineno, occ.end_lineno)
            print(f"  {where}  in {occ.qualname}")
        print()


# ── Argument parsing ─────────────────────────────────────────


def _bounded_int(minimum: int, label: str) -> Callable[[str], int]:
    def parse(value: str) -> int:
        parsed = int(value)
        if parsed < minimum:
            msg = f"{label} must be >= {minimum}"
            raise argparse.ArgumentTypeError(msg)
        return parsed

    return parse


_parse_min_count = _bounded_int(2, "min-count")
_parse_non_negative = _bounded_int(0, "value")
_parse_block_size = _bounded_int(2, "block-min-statements")


def _parse_threshold(value: str) -> float:
    parsed = float(value)
    if parsed < 0.0 or parsed > 1.0:
        msg = "threshold must be between 0 and 1"
        raise argparse.ArgumentTypeError(msg)
    return parsed


def _parse_optional_limit(value: str) -> int | str:
    if value.lower() == "none":
        return "none"
    return _parse_non_negative(value)


def _add_scan_args(parser: argparse.ArgumentParser, *, defaults: bool = True) -> None:
    """Options shared by every analysis command.

    With ``defaults=False`` every option defaults to ``None`` so that the
    check command can distinguish "not supplied" from an explicit value.
    """

    parser.add_argument(
        "--top-level-only",
        action=argparse.BooleanOptionalAction,
        default=False if defaults else None,
        help="Ignore nested functions and methods.",
    )
    parser.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=False if defaults else None,
        help="Fail on files that cannot be read or parsed.",
    )
    parser.add_argument(
        "--min-statements",
        type=_parse_non_negative,
        default=DEFAULT_MIN_STATEMENTS if defaults else None,
        help="Ignore functions with fewer statements than this.",
    )
    parser.add_argument(
        "--ignore-trivial",
        action=argparse.BooleanOptionalAction,
        default=True if defaults else None,
        help="Skip stubs, accessors, and call-free boilerplate.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[] if defaults else None,
        metavar="GLOB",
        help="Skip paths matching this glob, relative to root. Repeatable.",
    )


def _add_output_args(
    parser: argparse.ArgumentParser, *, formats: tuple[str, ...]
) -> None:
    parser.add_argument("--format", choices=formats, default=formats[0])
    parser.add_argument(
        "--output",
        help="Write JSON output to a file path. Requires --format json.",
    )


def _validate_output_arg(*, output_path: str | None, output_format: str) -> bool:
    if output_path and output_format != "json":
        print("Error: --output requires --format json.", file=sys.stderr)
        return False
    return True


def _add_normalization_args(
    parser: argparse.ArgumentParser, *, default: bool | None
) -> None:
    parser.add_argument(
        "--normalize-local-names",
        action=argparse.BooleanOptionalAction,
        default=default,
        help="Treat local variable renames as equivalent.",
    )
    parser.add_argument(
        "--normalize-constants",
        action=argparse.BooleanOptionalAction,
        default=default,
        help="Treat literal value changes as equivalent.",
    )


def _add_showcase_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--threshold", type=_parse_threshold, default=DEFAULT_THRESHOLD)
    parser.add_argument("--top-k", type=_parse_non_negative, default=5)
    parser.add_argument(
        "--block-min-statements",
        type=_parse_block_size,
        default=DEFAULT_BLOCK_MIN_STATEMENTS,
    )
    _add_scan_args(parser)
    _add_output_args(parser, formats=("text", "json"))


# ── Combined analysis ────────────────────────────────────────


class _Analysis:
    def __init__(
        self,
        *,
        root: Path,
        threshold: float,
        top_k: int | None,
        block_min_statements: int,
        normalize_local_names: bool,
        normalize_constants: bool,
        args: argparse.Namespace,
    ) -> None:
        self.scan_errors: list[str] = []
        self.plugin_errors: list[str] = []
        profiles = scan_functions(
            root,
            top_level_only=args.top_level_only,
            strict=args.strict,
            scan_errors=self.scan_errors,
            exclude=args.exclude,
        )
        common = {
            "min_statements": args.min_statements,
            "ignore_trivial": args.ignore_trivial,
            "profiles": profiles,
        }
        self.exact_rows = exact_groups(
            root,
            normalize_local_names=normalize_local_names,
            normalize_constants=normalize_constants,
            **common,
        )
        self.near_rows = near_matches(
            root,
            threshold=threshold,
            top_k=top_k,
            plugin_errors=self.plugin_errors,
            **common,
        )
        self.abstract_rows = [
            row
            for row in self.near_rows
            if row.suggested_refactor_kind != "leave_separate"
        ]
        self.block_rows = suppress_covered_blocks(
            block_clones(root, min_statements=block_min_statements, profiles=profiles),
            self.near_rows,
        )


def _summary(analysis: _Analysis) -> dict[str, int]:
    return {
        "exact_group_count": len(analysis.exact_rows),
        "near_count": len(analysis.near_rows),
        "abstract_count": len(analysis.abstract_rows),
        "block_clone_count": len(analysis.block_rows),
    }


def _showcase_payload(
    *, root: Path, threshold: float, top_k: int, analysis: _Analysis
) -> dict[str, Any]:
    clusters = cluster_near_matches(analysis.near_rows)
    return {
        "root": str(root),
        "settings": {
            "threshold": threshold,
            "top_k": top_k,
            "exact_normalization": {
                "normalize_local_names": True,
                "normalize_constants": True,
            },
        },
        "summary": _summary(analysis),
        "top_examples": {
            "exact": [
                {
                    "count": g.count,
                    "tier": g.tier,
                    "stmt_count": g.stmt_count,
                    "savings": g.savings,
                    "hash_prefix": g.hash[:12],
                    "qualnames": [occ.qualname for occ in g.occurrences],
                }
                for g in analysis.exact_rows[:top_k]
            ],
            "near": [
                {
                    "members": [occ.qualname for occ in c.members],
                    "shared_statements": c.shared_statements,
                    "savings": c.savings,
                    "priority": c.priority,
                    "suggested_refactor_kind": c.suggested_refactor_kind,
                }
                for c in clusters[:top_k]
            ],
            "blocks": [
                {
                    "stmt_count": g.stmt_count,
                    "count": g.count,
                    "savings": g.savings,
                    "locations": [
                        f"{occ.qualname}:{occ.lineno}-{occ.end_lineno}"
                        for occ in g.occurrences
                    ],
                }
                for g in analysis.block_rows[:top_k]
            ],
        },
    }


def _print_section(
    title: str, rows: list[dict[str, Any]], render: Callable[[dict[str, Any]], str]
) -> None:
    print(f"\n{title}")
    if not rows:
        print("  none")
        return
    for i, row in enumerate(rows, start=1):
        print(f"  {i}. {render(row)}")


def _print_showcase(payload: dict[str, Any]) -> None:
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

    _print_section("[1/3] Exact duplicates (whole functions)", top["exact"], exact_line)
    _print_section(
        "[2/3] Repeated blocks (inside larger functions)", top["blocks"], block_line
    )
    _print_section("[3/3] Near matches (ranked by priority)", top["near"], near_line)

    print("\nTip: rerun with --format json for machine-readable snapshots.")


def _report_payload(
    *,
    root: Path,
    threshold: float,
    top_k: int | None,
    normalize_local_names: bool,
    normalize_constants: bool,
    block_min_statements: int,
    analysis: _Analysis,
) -> dict[str, Any]:
    return {
        "root": str(root),
        "settings": {
            "threshold": threshold,
            "top_k": top_k,
            "block_min_statements": block_min_statements,
            "exact_normalization": {
                "normalize_local_names": normalize_local_names,
                "normalize_constants": normalize_constants,
            },
        },
        "summary": _summary(analysis),
        "exact": to_jsonable(analysis.exact_rows),
        "near": to_jsonable(analysis.near_rows),
        "abstract": to_jsonable(analysis.abstract_rows),
        "blocks": to_jsonable(analysis.block_rows),
    }


# ── Parser ───────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="pydry",
        description=(
            "AST-based duplicate and structural similarity detector for Python."
        ),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def analysis_parser(name: str, help_text: str) -> argparse.ArgumentParser:
        parser = sub.add_parser(name, help=help_text)
        parser.add_argument("root")
        return parser

    def finish(parser: argparse.ArgumentParser, *, formats: tuple[str, ...]) -> None:
        _add_scan_args(parser)
        _add_output_args(parser, formats=formats)

    def add_threshold(parser: argparse.ArgumentParser, default: float) -> None:
        parser.add_argument("--threshold", type=_parse_threshold, default=default)

    def add_block_size(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--block-min-statements",
            type=_parse_block_size,
            default=DEFAULT_BLOCK_MIN_STATEMENTS,
            help="Minimum consecutive statements for a repeated block.",
        )

    p_exact = analysis_parser(
        "exact", "Find whole functions that are duplicates under normalization."
    )
    p_exact.add_argument("-n", "--min-count", type=_parse_min_count, default=2)
    _add_normalization_args(p_exact, default=True)
    p_exact.add_argument("--include-canonical", action="store_true")
    finish(p_exact, formats=("text", "json"))

    p_near = analysis_parser("near", "Rank structurally similar functions.")
    add_threshold(p_near, DEFAULT_THRESHOLD)
    p_near.add_argument("--top-k", type=_parse_non_negative, default=None)
    finish(p_near, formats=("text", "json"))

    p_abs = analysis_parser("abstract", "Report near matches that look consolidatable.")
    add_threshold(p_abs, 0.82)
    p_abs.add_argument("--top-k", type=_parse_non_negative, default=None)
    finish(p_abs, formats=("text", "json"))

    p_blocks = analysis_parser(
        "blocks", "Find runs of statements repeated inside larger functions."
    )
    add_block_size(p_blocks)
    finish(p_blocks, formats=("text", "json"))

    p_report = analysis_parser(
        "report",
        "Generate one JSON report with exact, block, near, and abstract results.",
    )
    add_threshold(p_report, DEFAULT_THRESHOLD)
    p_report.add_argument("--top-k", type=_parse_non_negative, default=200)
    add_block_size(p_report)
    _add_normalization_args(p_report, default=True)
    finish(p_report, formats=("json",))

    p_check = sub.add_parser(
        "check", help="Evaluate repository findings against a configurable CI policy."
    )
    p_check.add_argument("root", nargs="?", default=None)
    p_check.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Read settings from this standalone pydry TOML file.",
    )
    p_check.add_argument("--profile", choices=sorted(PROFILES), default=None)
    p_check.add_argument("--threshold", type=_parse_threshold, default=None)
    p_check.add_argument("--top-k", type=_parse_non_negative, default=None)
    p_check.add_argument("--block-min-statements", type=_parse_block_size, default=None)
    _add_normalization_args(p_check, default=None)
    _add_scan_args(p_check, defaults=False)
    p_check.add_argument("--max-exact-groups", type=_parse_optional_limit, default=None)
    p_check.add_argument("--max-block-clones", type=_parse_optional_limit, default=None)
    p_check.add_argument("--max-near-matches", type=_parse_optional_limit, default=None)
    p_check.add_argument(
        "--max-abstract-candidates", type=_parse_optional_limit, default=None
    )
    p_check.add_argument(
        "--fail-on-scan-errors", action=argparse.BooleanOptionalAction, default=None
    )
    p_check.add_argument(
        "--fail-on-plugin-errors", action=argparse.BooleanOptionalAction, default=None
    )
    p_check.add_argument("--annotation-limit", type=_parse_non_negative, default=None)
    p_check.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="Ignore findings recorded in this baseline file.",
    )
    p_check.add_argument(
        "--update-baseline",
        action="store_true",
        help="Record the current findings as the accepted baseline and pass.",
    )
    p_check.add_argument(
        "--output",
        type=Path,
        default=Path("pydry-report.json"),
        help="Write the complete JSON check report to this path.",
    )
    p_check.add_argument(
        "--github",
        action="store_true",
        help="Emit GitHub annotations, job summary, and action outputs.",
    )

    p_show = sub.add_parser(
        "showcase", help="Print a compact summary of a corpus (default: current dir)."
    )
    _add_showcase_args(p_show)
    p_sim = sub.add_parser("simulate", help="Alias for showcase.")
    _add_showcase_args(p_sim)
    return ap


# ── Entry point ──────────────────────────────────────────────


def _run_check_command(args: argparse.Namespace) -> int:
    try:
        config = load_check_config(args.config)
        config_path = args.config
        if config_path is None and Path(CONFIG_FILENAME).is_file():
            config_path = Path(CONFIG_FILENAME)
        config = apply_overrides(
            config,
            root=args.root,
            profile=args.profile,
            threshold=args.threshold,
            top_k=args.top_k,
            top_level_only=args.top_level_only,
            strict=args.strict,
            normalize_local_names=args.normalize_local_names,
            normalize_constants=args.normalize_constants,
            min_statements=args.min_statements,
            ignore_trivial=args.ignore_trivial,
            block_min_statements=args.block_min_statements,
            exclude=args.exclude,
            baseline=str(args.baseline) if args.baseline is not None else None,
            max_exact_groups=args.max_exact_groups,
            max_block_clones=args.max_block_clones,
            max_near_matches=args.max_near_matches,
            max_abstract_candidates=args.max_abstract_candidates,
            fail_on_scan_errors=args.fail_on_scan_errors,
            fail_on_plugin_errors=args.fail_on_plugin_errors,
            annotation_limit=args.annotation_limit,
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return run_check(
        root=Path(config.root),
        config=config,
        output_path=args.output,
        github=args.github,
        config_path=config_path,
        baseline_path=Path(config.baseline) if config.baseline else None,
        update_baseline=args.update_baseline,
    )


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    args = _build_parser().parse_args(argv)

    if args.cmd == "check":
        return _run_check_command(args)

    root = Path(args.root)
    if not root.exists() or not root.is_dir():
        print(f"Invalid directory: {root}", file=sys.stderr)
        return 2
    if not _validate_output_arg(output_path=args.output, output_format=args.format):
        return 2

    scan_errors: list[str] = []
    plugin_errors: list[str] = []
    scan_kwargs = {
        "top_level_only": args.top_level_only,
        "strict": args.strict,
        "scan_errors": scan_errors,
        "min_statements": args.min_statements,
        "ignore_trivial": args.ignore_trivial,
        "exclude": args.exclude,
    }

    try:
        if args.cmd == "exact":
            payload: object = exact_groups(
                root,
                min_count=args.min_count,
                include_canonical=args.include_canonical,
                normalize_local_names=args.normalize_local_names,
                normalize_constants=args.normalize_constants,
                **scan_kwargs,
            )
        elif args.cmd in {"near", "abstract"}:
            rows = near_matches(
                root,
                threshold=args.threshold,
                top_k=None,
                plugin_errors=plugin_errors,
                **scan_kwargs,
            )
            if args.cmd == "abstract":
                rows = [
                    r for r in rows if r.suggested_refactor_kind != "leave_separate"
                ]
            payload = rows[: args.top_k] if args.top_k is not None else rows
        elif args.cmd == "blocks":
            payload = block_clones(
                root,
                min_statements=args.block_min_statements,
                top_level_only=args.top_level_only,
                strict=args.strict,
                scan_errors=scan_errors,
                exclude=args.exclude,
            )
        else:
            analysis = _Analysis(
                root=root,
                threshold=args.threshold,
                top_k=None,
                block_min_statements=args.block_min_statements,
                normalize_local_names=getattr(args, "normalize_local_names", True),
                normalize_constants=getattr(args, "normalize_constants", True),
                args=args,
            )
            scan_errors = analysis.scan_errors
            plugin_errors = analysis.plugin_errors
            if args.cmd == "report":
                payload = _report_payload(
                    root=root,
                    threshold=args.threshold,
                    top_k=args.top_k,
                    normalize_local_names=args.normalize_local_names,
                    normalize_constants=args.normalize_constants,
                    block_min_statements=args.block_min_statements,
                    analysis=analysis,
                )
                if args.top_k is not None:
                    for key in ("near", "abstract", "blocks"):
                        payload[key] = payload[key][: args.top_k]
            else:
                payload = _showcase_payload(
                    root=root,
                    threshold=args.threshold,
                    top_k=args.top_k,
                    analysis=analysis,
                )
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    _print_diagnostics(scan_errors, plugin_errors)
    if args.format == "json":
        _emit_json_output(
            to_jsonable(payload),
            scan_errors=scan_errors,
            plugin_errors=plugin_errors,
            output_path=args.output,
        )
        return 0

    if args.cmd == "exact":
        _print_exact(payload)  # type: ignore[arg-type]
    elif args.cmd in {"near", "abstract"}:
        _print_near(payload)  # type: ignore[arg-type]
    elif args.cmd == "blocks":
        _print_blocks(payload)  # type: ignore[arg-type]
    else:
        _print_showcase(payload)  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
