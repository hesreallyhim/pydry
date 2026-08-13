from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .check import run_check
from .config import CONFIG_FILENAME, ConfigError, apply_overrides, load_check_config
from .engine import abstract_candidates, exact_groups, near_matches, to_jsonable

if TYPE_CHECKING:
    from .models import ExactGroup, SimilarityResult


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


def _json_envelope(
    payload: object,
    *,
    scan_errors: list[str],
    plugin_errors: list[str],
) -> dict[str, object]:
    return {
        "results": payload,
        "diagnostics": _diagnostics_payload(scan_errors, plugin_errors),
    }


def _emit_json_output(
    payload: object,
    *,
    scan_errors: list[str],
    plugin_errors: list[str],
    output_path: str | None,
) -> None:
    envelope = _json_envelope(
        payload,
        scan_errors=scan_errors,
        plugin_errors=plugin_errors,
    )
    rendered = json.dumps(envelope, indent=2)
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote JSON report to {out}", file=sys.stderr)
        return
    print(rendered)


def _parse_min_count(value: str) -> int:
    parsed = int(value)
    if parsed < 2:
        msg = "min-count must be >= 2"
        raise argparse.ArgumentTypeError(msg)
    return parsed


def _parse_threshold(value: str) -> float:
    parsed = float(value)
    if parsed < 0.0 or parsed > 1.0:
        msg = "threshold must be between 0 and 1"
        raise argparse.ArgumentTypeError(msg)
    return parsed


def _parse_top_k(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        msg = "top-k must be >= 0"
        raise argparse.ArgumentTypeError(msg)
    return parsed


def _parse_non_negative(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        msg = "value must be >= 0"
        raise argparse.ArgumentTypeError(msg)
    return parsed


def _parse_optional_limit(value: str) -> int | str:
    if value.lower() == "none":
        return "none"
    return _parse_non_negative(value)


def _add_json_output_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output",
        help=("Write JSON output to a file path. Requires --format json."),
    )


def _validate_output_arg(
    *,
    output_path: str | None,
    output_format: str,
) -> bool:
    if output_path and output_format != "json":
        print("Error: --output requires --format json.", file=sys.stderr)
        return False
    return True


def _print_diagnostics(scan_errors: list[str], plugin_errors: list[str]) -> None:
    if scan_errors:
        print(
            (
                f"Warning: skipped {len(scan_errors)} file(s) due to parse/read errors."
                " Use --strict to fail instead."
            ),
            file=sys.stderr,
        )
        for msg in scan_errors[:5]:
            print(f"  - {msg}", file=sys.stderr)
        if len(scan_errors) > 5:
            print(f"  ... {len(scan_errors) - 5} more", file=sys.stderr)

    if plugin_errors:
        unique_errors = list(dict.fromkeys(plugin_errors))
        print(
            (
                f"Warning: {len(unique_errors)} plugin error(s) occurred."
                " Plugin failures were isolated."
            ),
            file=sys.stderr,
        )
        for msg in unique_errors[:5]:
            print(f"  - {msg}", file=sys.stderr)
        if len(unique_errors) > 5:
            print(f"  ... {len(unique_errors) - 5} more", file=sys.stderr)


def _print_exact(groups: list[ExactGroup]) -> None:
    if not groups:
        print("No duplicate functions found.")
        return
    for i, g in enumerate(groups, start=1):
        print(f"Group {i}: {g.count} occurrences  hash={g.hash[:12]}")
        for occ in g.occurrences:
            end = f"-{occ.end_lineno}" if occ.end_lineno else ""
            print(f"  {occ.path}:{occ.lineno}{end}  {occ.kind} {occ.qualname}")
        print()


def _print_near(rows: list[SimilarityResult]) -> None:
    if not rows:
        print("No near matches found.")
        return
    for i, r in enumerate(rows, start=1):
        print(
            f"{i}. sim={r.similarity_score:.4f}"
            f" refactor={r.refactorability_score:.4f}"
            f"  {r.a.qualname}  <->  {r.b.qualname}"
        )
        print(f"   refactor: {r.suggested_refactor_kind}")
        if r.pattern_labels:
            print("   labels: " + ", ".join(r.pattern_labels))
        if r.risk_flags:
            print("   risks: " + ", ".join(r.risk_flags))
        print(f"   shared: {r.shared_structure_summary}")
        if r.key_differences:
            print("   diffs: " + "; ".join(r.key_differences))
        print()


def _dedupe_messages(messages: list[str]) -> list[str]:
    return list(dict.fromkeys(messages))


def _showcase_pair_rows(
    rows: list[SimilarityResult], *, top_k: int
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for row in rows[:top_k]:
        out.append(
            {
                "similarity_score": row.similarity_score,
                "refactorability_score": row.refactorability_score,
                "a": row.a.qualname,
                "b": row.b.qualname,
                "suggested_refactor_kind": row.suggested_refactor_kind,
                "pattern_labels": row.pattern_labels,
                "risk_flags": row.risk_flags,
            }
        )
    return out


def _showcase_exact_rows(
    groups: list[ExactGroup], *, top_k: int
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for group in groups[:top_k]:
        out.append(
            {
                "count": group.count,
                "hash_prefix": group.hash[:12],
                "qualnames": [occ.qualname for occ in group.occurrences],
            }
        )
    return out


def _showcase_payload(
    *,
    root: Path,
    threshold: float,
    top_k: int,
    exact_rows: list[ExactGroup],
    near_rows: list[SimilarityResult],
    abstract_rows: list[SimilarityResult],
) -> dict[str, Any]:
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
        "summary": {
            "exact_group_count": len(exact_rows),
            "near_count": len(near_rows),
            "abstract_count": len(abstract_rows),
        },
        "top_examples": {
            "exact": _showcase_exact_rows(exact_rows, top_k=top_k),
            "near": _showcase_pair_rows(near_rows, top_k=top_k),
            "abstract": _showcase_pair_rows(abstract_rows, top_k=top_k),
        },
    }


def _report_payload(
    *,
    root: Path,
    threshold: float,
    top_k: int | None,
    normalize_local_names: bool,
    normalize_constants: bool,
    exact_rows: list[ExactGroup],
    near_rows: list[SimilarityResult],
    abstract_rows: list[SimilarityResult],
) -> dict[str, Any]:
    return {
        "root": str(root),
        "settings": {
            "threshold": threshold,
            "top_k": top_k,
            "exact_normalization": {
                "normalize_local_names": normalize_local_names,
                "normalize_constants": normalize_constants,
            },
        },
        "summary": {
            "exact_group_count": len(exact_rows),
            "near_count": len(near_rows),
            "abstract_count": len(abstract_rows),
        },
        "exact": to_jsonable(exact_rows),
        "near": to_jsonable(near_rows),
        "abstract": to_jsonable(abstract_rows),
    }


def _print_showcase(payload: dict[str, Any]) -> None:
    def _score_bar(score: float, *, width: int = 18) -> str:
        clamped = max(0.0, min(1.0, score))
        filled = round(clamped * width)
        return "[" + ("#" * filled) + ("." * (width - filled)) + "]"

    summary = payload["summary"]
    top_examples = payload["top_examples"]
    settings = payload["settings"]

    print("=" * 72)
    print("PYDRY SHOWCASE SIMULATION")
    print("=" * 72)
    print(f"Corpus: {payload['root']}")
    print(
        "Summary: "
        f"exact_groups={summary['exact_group_count']} "
        f"near_pairs={summary['near_count']} "
        f"abstract_candidates={summary['abstract_count']}"
    )
    print(f"Config: threshold={settings['threshold']} top_k={settings['top_k']}")

    print("\n[1/3] Exact duplicate discovery")
    print(
        "  command: pydry exact <corpus> --normalize-local-names --normalize-constants"
    )
    if top_examples["exact"]:
        for i, group in enumerate(top_examples["exact"], start=1):
            names = ", ".join(group["qualnames"])
            print(f"  {i}. count={group['count']} hash={group['hash_prefix']} {names}")
    else:
        print("  none")

    print("\n[2/3] Near-match ranking")
    print(f"  command: pydry near <corpus> --threshold {settings['threshold']}")
    if top_examples["near"]:
        for i, row in enumerate(top_examples["near"], start=1):
            sim_bar = _score_bar(row["similarity_score"])
            ref_bar = _score_bar(row["refactorability_score"])
            print(
                f"  {i}. {row['a']} <-> {row['b']} ({row['suggested_refactor_kind']})"
            )
            print(f"     sim      {sim_bar} {row['similarity_score']:.4f}")
            print(f"     refactor {ref_bar} {row['refactorability_score']:.4f}")
            if row["pattern_labels"]:
                print("     labels   " + ", ".join(row["pattern_labels"]))
            if row["risk_flags"]:
                print("     risks    " + ", ".join(row["risk_flags"]))
    else:
        print("  none")

    print("\n[3/3] Abstraction candidates")
    print(f"  command: pydry abstract <corpus> --threshold {settings['threshold']}")
    if top_examples["abstract"]:
        for i, row in enumerate(top_examples["abstract"], start=1):
            sim_bar = _score_bar(row["similarity_score"])
            ref_bar = _score_bar(row["refactorability_score"])
            print(
                f"  {i}. {row['a']} <-> {row['b']} ({row['suggested_refactor_kind']})"
            )
            print(f"     sim      {sim_bar} {row['similarity_score']:.4f}")
            print(f"     refactor {ref_bar} {row['refactorability_score']:.4f}")
            if row["pattern_labels"]:
                print("     labels   " + ", ".join(row["pattern_labels"]))
    else:
        print("  none")

    print("\nTip: rerun with --format json for machine-readable snapshots.")


def _add_showcase_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--threshold", type=_parse_threshold, default=0.75)
    parser.add_argument("--top-k", type=_parse_top_k, default=5)
    parser.add_argument("--top-level-only", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    _add_json_output_arg(parser)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(
        prog="pydry",
        description=(
            "AST-based duplicate and structural similarity detector for Python."
        ),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_exact = sub.add_parser(
        "exact",
        help="Find exact structural duplicates under configurable normalization.",
    )
    p_exact.add_argument("root")
    p_exact.add_argument("-n", "--min-count", type=_parse_min_count, default=2)
    p_exact.add_argument("--top-level-only", action="store_true")
    p_exact.add_argument("--normalize-local-names", action="store_true")
    p_exact.add_argument("--normalize-constants", action="store_true")
    p_exact.add_argument("--include-canonical", action="store_true")
    p_exact.add_argument("--strict", action="store_true")
    p_exact.add_argument("--format", choices=("text", "json"), default="text")
    _add_json_output_arg(p_exact)

    p_near = sub.add_parser("near", help="Rank structurally similar functions.")
    p_near.add_argument("root")
    p_near.add_argument("--threshold", type=_parse_threshold, default=0.8)
    p_near.add_argument("--top-k", type=_parse_top_k, default=None)
    p_near.add_argument("--top-level-only", action="store_true")
    p_near.add_argument("--strict", action="store_true")
    p_near.add_argument("--format", choices=("text", "json"), default="text")
    _add_json_output_arg(p_near)

    p_abs = sub.add_parser(
        "abstract", help="Report likely abstraction/refactor candidates."
    )
    p_abs.add_argument("root")
    p_abs.add_argument("--threshold", type=_parse_threshold, default=0.82)
    p_abs.add_argument("--top-k", type=_parse_top_k, default=None)
    p_abs.add_argument("--top-level-only", action="store_true")
    p_abs.add_argument("--strict", action="store_true")
    p_abs.add_argument("--format", choices=("text", "json"), default="text")
    _add_json_output_arg(p_abs)

    p_report = sub.add_parser(
        "report",
        help=(
            "Generate a single machine-readable report combining exact, near, "
            "and abstraction-candidate results."
        ),
    )
    p_report.add_argument("root")
    p_report.add_argument("--threshold", type=_parse_threshold, default=0.8)
    p_report.add_argument("--top-k", type=_parse_top_k, default=200)
    p_report.add_argument("--top-level-only", action="store_true")
    p_report.add_argument("--strict", action="store_true")
    p_report.set_defaults(normalize_local_names=True, normalize_constants=True)
    p_report.add_argument(
        "--no-normalize-local-names",
        action="store_false",
        dest="normalize_local_names",
        help="Disable local-name normalization for exact-group analysis.",
    )
    p_report.add_argument(
        "--no-normalize-constants",
        action="store_false",
        dest="normalize_constants",
        help="Disable constant normalization for exact-group analysis.",
    )
    p_report.add_argument("--format", choices=("json",), default="json")
    _add_json_output_arg(p_report)

    p_check = sub.add_parser(
        "check",
        help="Evaluate repository findings against a configurable CI policy.",
    )
    p_check.add_argument("root", nargs="?", default=None)
    p_check.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Read settings from this standalone pydry TOML file.",
    )
    p_check.add_argument("--threshold", type=_parse_threshold, default=None)
    p_check.add_argument("--top-k", type=_parse_top_k, default=None)
    p_check.add_argument(
        "--top-level-only", action=argparse.BooleanOptionalAction, default=None
    )
    p_check.add_argument(
        "--strict", action=argparse.BooleanOptionalAction, default=None
    )
    p_check.add_argument(
        "--normalize-local-names",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    p_check.add_argument(
        "--normalize-constants",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    p_check.add_argument("--max-exact-groups", type=_parse_optional_limit, default=None)
    p_check.add_argument("--max-near-matches", type=_parse_optional_limit, default=None)
    p_check.add_argument(
        "--max-abstract-candidates", type=_parse_optional_limit, default=None
    )
    p_check.add_argument(
        "--fail-on-scan-errors",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    p_check.add_argument(
        "--fail-on-plugin-errors",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    p_check.add_argument("--annotation-limit", type=_parse_non_negative, default=None)
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
        "showcase",
        help=(
            "Run a compact summary over a corpus (defaults to the current directory)."
        ),
    )
    _add_showcase_args(p_show)

    p_sim = sub.add_parser(
        "simulate",
        help=(
            "Run a visual terminal simulation of duplicate detection and "
            "refactor-candidate ranking."
        ),
    )
    _add_showcase_args(p_sim)

    args = ap.parse_args(argv)

    if args.cmd == "check":
        try:
            config = load_check_config(args.config)
            config_path = args.config
            if config_path is None and Path(CONFIG_FILENAME).is_file():
                config_path = Path(CONFIG_FILENAME)
            config = apply_overrides(
                config,
                root=args.root,
                threshold=args.threshold,
                top_k=args.top_k,
                top_level_only=args.top_level_only,
                strict=args.strict,
                normalize_local_names=args.normalize_local_names,
                normalize_constants=args.normalize_constants,
                max_exact_groups=args.max_exact_groups,
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
        )

    root = Path(args.root)
    if not root.exists() or not root.is_dir():
        print(f"Invalid directory: {root}", file=sys.stderr)
        return 2

    scan_errors: list[str] = []
    plugin_errors: list[str] = []

    if args.cmd == "exact":
        if not _validate_output_arg(output_path=args.output, output_format=args.format):
            return 2
        try:
            exact_rows = exact_groups(
                root,
                min_count=args.min_count,
                top_level_only=args.top_level_only,
                include_canonical=args.include_canonical,
                normalize_local_names=args.normalize_local_names,
                normalize_constants=args.normalize_constants,
                strict=args.strict,
                scan_errors=scan_errors,
            )
        except (RuntimeError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        _print_diagnostics(scan_errors, plugin_errors)
        if args.format == "json":
            _emit_json_output(
                to_jsonable(exact_rows),
                scan_errors=scan_errors,
                plugin_errors=plugin_errors,
                output_path=args.output,
            )
        else:
            _print_exact(exact_rows)
        return 0

    if args.cmd == "near":
        if not _validate_output_arg(output_path=args.output, output_format=args.format):
            return 2
        try:
            near_rows = near_matches(
                root,
                threshold=args.threshold,
                top_k=args.top_k,
                top_level_only=args.top_level_only,
                strict=args.strict,
                scan_errors=scan_errors,
                plugin_errors=plugin_errors,
            )
        except (RuntimeError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        _print_diagnostics(scan_errors, plugin_errors)
        if args.format == "json":
            _emit_json_output(
                to_jsonable(near_rows),
                scan_errors=scan_errors,
                plugin_errors=plugin_errors,
                output_path=args.output,
            )
        else:
            _print_near(near_rows)
        return 0

    if args.cmd == "abstract":
        if not _validate_output_arg(output_path=args.output, output_format=args.format):
            return 2
        try:
            abstract_rows = abstract_candidates(
                root,
                threshold=args.threshold,
                top_k=args.top_k,
                top_level_only=args.top_level_only,
                strict=args.strict,
                scan_errors=scan_errors,
                plugin_errors=plugin_errors,
            )
        except (RuntimeError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        _print_diagnostics(scan_errors, plugin_errors)
        if args.format == "json":
            _emit_json_output(
                to_jsonable(abstract_rows),
                scan_errors=scan_errors,
                plugin_errors=plugin_errors,
                output_path=args.output,
            )
        else:
            _print_near(abstract_rows)
        return 0

    if args.cmd in {"showcase", "simulate"}:
        if not _validate_output_arg(output_path=args.output, output_format=args.format):
            return 2
        exact_scan_errors: list[str] = []
        near_scan_errors: list[str] = []
        near_plugin_errors: list[str] = []
        try:
            exact_rows = exact_groups(
                root,
                min_count=2,
                top_level_only=args.top_level_only,
                include_canonical=False,
                normalize_local_names=True,
                normalize_constants=True,
                strict=args.strict,
                scan_errors=exact_scan_errors,
            )
            near_rows = near_matches(
                root,
                threshold=args.threshold,
                top_k=None,
                top_level_only=args.top_level_only,
                strict=args.strict,
                scan_errors=near_scan_errors,
                plugin_errors=near_plugin_errors,
            )
        except (RuntimeError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

        abstract_rows = [
            row for row in near_rows if row.suggested_refactor_kind != "leave_separate"
        ]
        combined_scan_errors = _dedupe_messages([*exact_scan_errors, *near_scan_errors])
        combined_plugin_errors = _dedupe_messages(near_plugin_errors)
        _print_diagnostics(combined_scan_errors, combined_plugin_errors)

        payload = _showcase_payload(
            root=root,
            threshold=args.threshold,
            top_k=args.top_k,
            exact_rows=exact_rows,
            near_rows=near_rows,
            abstract_rows=abstract_rows,
        )
        if args.format == "json":
            _emit_json_output(
                payload,
                scan_errors=combined_scan_errors,
                plugin_errors=combined_plugin_errors,
                output_path=args.output,
            )
        else:
            _print_showcase(payload)
        return 0

    if args.cmd == "report":
        if not _validate_output_arg(output_path=args.output, output_format=args.format):
            return 2
        report_exact_scan_errors: list[str] = []
        report_near_scan_errors: list[str] = []
        report_near_plugin_errors: list[str] = []
        try:
            exact_rows = exact_groups(
                root,
                min_count=2,
                top_level_only=args.top_level_only,
                include_canonical=False,
                normalize_local_names=args.normalize_local_names,
                normalize_constants=args.normalize_constants,
                strict=args.strict,
                scan_errors=report_exact_scan_errors,
            )
            near_rows = near_matches(
                root,
                threshold=args.threshold,
                top_k=args.top_k,
                top_level_only=args.top_level_only,
                strict=args.strict,
                scan_errors=report_near_scan_errors,
                plugin_errors=report_near_plugin_errors,
            )
        except (RuntimeError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

        abstract_rows = [
            row for row in near_rows if row.suggested_refactor_kind != "leave_separate"
        ]
        report_scan_errors = _dedupe_messages(
            [*report_exact_scan_errors, *report_near_scan_errors]
        )
        report_plugin_errors = _dedupe_messages(report_near_plugin_errors)
        _print_diagnostics(report_scan_errors, report_plugin_errors)
        payload = _report_payload(
            root=root,
            threshold=args.threshold,
            top_k=args.top_k,
            normalize_local_names=args.normalize_local_names,
            normalize_constants=args.normalize_constants,
            exact_rows=exact_rows,
            near_rows=near_rows,
            abstract_rows=abstract_rows,
        )
        _emit_json_output(
            payload,
            scan_errors=report_scan_errors,
            plugin_errors=report_plugin_errors,
            output_path=args.output,
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
