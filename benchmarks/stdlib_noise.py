"""Measure pydry's signal-to-noise on real code.

Runs the engine against packages from the running interpreter's standard
library (they ship with every Python and are reasonably well maintained, so
most findings there are the kind a reviewer would rather not see) and prints
finding counts and a sample of the top results per setting. Use it when
tuning defaults: a change that halves the count on the standard library
while keeping the demo corpus findings is a good change.

Usage:

    python benchmarks/stdlib_noise.py                  # default settings
    python benchmarks/stdlib_noise.py --threshold 0.7  # sweep one setting
    python benchmarks/stdlib_noise.py --packages email asyncio json --show 5
"""

from __future__ import annotations

import argparse
import importlib
import os
import time
from pathlib import Path

from pydry.engine import (
    block_clones,
    exact_groups,
    near_matches,
    scan_functions,
    suppress_covered_blocks,
)

DEFAULT_PACKAGES = ("email", "asyncio", "json", "http", "unittest", "logging")


def _package_dir(name: str) -> Path:
    module = importlib.import_module(name)
    file = module.__file__
    if file is None:
        raise SystemExit(f"{name} has no source directory")
    return Path(os.path.dirname(file))


def run(
    name: str,
    *,
    threshold: float,
    min_statements: int,
    block_min_statements: int,
    ignore_trivial: bool,
    show: int,
) -> None:
    root = _package_dir(name)
    started = time.perf_counter()
    profiles = scan_functions(root)
    eligible = [
        p
        for p in profiles
        if p.eligible(min_statements=min_statements, ignore_trivial=ignore_trivial)
    ]
    exact = exact_groups(
        root,
        profiles=profiles,
        min_statements=min_statements,
        ignore_trivial=ignore_trivial,
    )
    near = near_matches(
        root,
        profiles=profiles,
        threshold=threshold,
        min_statements=min_statements,
        ignore_trivial=ignore_trivial,
    )
    blocks = suppress_covered_blocks(
        block_clones(root, profiles=profiles, min_statements=block_min_statements),
        near,
    )
    elapsed = time.perf_counter() - started

    print(
        f"== {name}: {len(profiles)} functions ({len(eligible)} analyzed),"
        f" {elapsed:.1f}s; exact={len(exact)} blocks={len(blocks)} near={len(near)}"
    )
    for group in exact[:show]:
        names = ", ".join(o.qualname for o in group.occurrences[:4])
        print(
            f"   exact  {group.tier:9} x{group.count} {group.stmt_count:>3} stmts"
            f"  saves {group.savings:>3}  {names}"
        )
    for block in blocks[:show]:
        where = ", ".join(
            f"{o.qualname}:{o.lineno}" for o in block.occurrences[:3]
        )
        print(
            f"   block  {block.stmt_count:>3} stmts x{block.count}"
            f"  saves {block.savings:>3}  {where}"
        )
    for row in near[:show]:
        print(
            f"   near   pri={row.priority:>5} sim={row.similarity_score:.2f}"
            f" shared={row.shared_statements:>3}"
            f"  {row.a.qualname} ~ {row.b.qualname}"
            f"  [{', '.join(row.pattern_labels)}] -> {row.suggested_refactor_kind}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--packages", nargs="+", default=list(DEFAULT_PACKAGES))
    parser.add_argument("--threshold", type=float, default=0.8)
    parser.add_argument("--min-statements", type=int, default=2)
    parser.add_argument("--block-min-statements", type=int, default=6)
    parser.add_argument("--include-trivial", action="store_true")
    parser.add_argument("--show", type=int, default=3)
    args = parser.parse_args()
    for name in args.packages:
        run(
            name,
            threshold=args.threshold,
            min_statements=args.min_statements,
            block_min_statements=args.block_min_statements,
            ignore_trivial=not args.include_trivial,
            show=args.show,
        )


if __name__ == "__main__":
    main()
