"""Repeated-block detection: statement runs copied into two or more places."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from .models import BlockCloneGroup, BlockOccurrence
from .scan import DEFAULT_THRESHOLD, resolve_profiles, sha, tier_hash

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from .analyze import FunctionProfile
    from .canonical import StmtToken
    from .models import SimilarityResult

DEFAULT_BLOCK_MIN_STATEMENTS = 6

_MAX_BUCKET_PAIRS = 40


def _run_hash(tokens: list[StmtToken], start: int, length: int) -> str:
    base = tokens[start].depth
    body = "\n".join(
        f"{token.depth - base}:{token.full}" for token in tokens[start : start + length]
    )
    return sha(body)


def _same_token(ta: StmtToken, tb: StmtToken, base_a: int, base_b: int) -> bool:
    return ta.depth - base_a == tb.depth - base_b and ta.full == tb.full


def _run_summary(tokens: list[StmtToken]) -> str:
    kinds = Counter(token.kind for token in tokens)
    return ", ".join(f"{kind} x{count}" for kind, count in kinds.most_common(4))


def _maximal_run(
    ta: list[StmtToken], ia: int, tb: list[StmtToken], ib: int, *, same_function: bool
) -> tuple[int, int, int]:
    """Extend a matching window in both directions.

    Returns ``(start_a, start_b, length)``. Depths are compared relative to
    the seed positions, so the run may start and end at any nesting level as
    long as both sides nest the same way. Within one function the two ranges
    are kept from overlapping.
    """

    base_a, base_b = ta[ia].depth, tb[ib].depth
    start_a, start_b = ia, ib
    while (
        start_a > 0
        and start_b > 0
        and _same_token(ta[start_a - 1], tb[start_b - 1], base_a, base_b)
    ):
        start_a -= 1
        start_b -= 1
    limit = min(len(ta) - start_a, len(tb) - start_b)
    if same_function:
        limit = min(limit, abs(start_a - start_b))
    length = ia - start_a
    while length < limit and _same_token(
        ta[start_a + length], tb[start_b + length], base_a, base_b
    ):
        length += 1
    return start_a, start_b, length


def _covered_by_near_match(
    ta: list[StmtToken],
    start_a: int,
    tb: list[StmtToken],
    start_b: int,
    length: int,
    threshold: float,
) -> bool:
    if threshold <= 0.0 or ta[start_a].depth != tb[start_b].depth:
        return False
    return length >= threshold * len(ta) and length >= threshold * len(tb)


def block_clones(
    root: Path,
    *,
    min_statements: int = DEFAULT_BLOCK_MIN_STATEMENTS,
    near_threshold: float = DEFAULT_THRESHOLD,
    top_level_only: bool = False,
    strict: bool = False,
    scan_errors: list[str] | None = None,
    exclude: Sequence[str] = (),
    profiles: list[FunctionProfile] | None = None,
) -> list[BlockCloneGroup]:
    """Find runs of ``min_statements`` or more statements repeated verbatim.

    Runs are compared on the fully normalized statement form, so renamed
    locals and changed constants still match. Every window of
    ``min_statements`` tokens is hashed; windows that hash the same are
    extended in both directions to a maximal run, and runs are grouped by
    content. Nested runs already inside a longer reported run are dropped.

    A run that starts at the same nesting depth in both functions and covers
    at least ``near_threshold`` of each implies a near-match similarity of at
    least that value, so such runs are left to the near-match report. Runs
    at different depths (the same statements once inside an ``if``) are kept,
    because near matching compares absolute depth and would not align them.
    Callers that combine the two reports should pass the same threshold they
    use for near matches; ``0`` disables the rule.

    When one window hashes the same in more than ``_MAX_BUCKET_PAIRS``
    places, each occurrence is only extended against the first one instead
    of pairwise. Occurrences whose maximal run differs from the first
    occurrence's therefore land in separate, possibly shorter, groups. This
    keeps very common windows (identical boilerplate repeated dozens of
    times) from costing quadratic time.
    """

    if min_statements < 2:
        msg = "min_statements must be >= 2"
        raise ValueError(msg)
    if not 0.0 <= near_threshold <= 1.0:
        msg = "near_threshold must be between 0 and 1"
        raise ValueError(msg)
    items = resolve_profiles(
        root,
        profiles,
        top_level_only=top_level_only,
        strict=strict,
        scan_errors=scan_errors,
        exclude=exclude,
    )
    items = sorted(
        (p for p in items if p.stmt_count >= min_statements),
        key=lambda p: (p.occurrence.path, p.occurrence.lineno),
    )
    k = min_statements

    buckets: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for index, profile in enumerate(items):
        tokens = profile.tokens
        for start in range(len(tokens) - k + 1):
            buckets[_run_hash(tokens, start, k)].append((index, start))

    runs: dict[str, dict[tuple[int, int, int], None]] = defaultdict(dict)
    run_tokens: dict[str, list[StmtToken]] = {}
    seen: set[tuple[int, int, int, int]] = set()
    for entries in buckets.values():
        if len(entries) < 2:
            continue
        if len(entries) > _MAX_BUCKET_PAIRS:
            pairs = [(entries[0], other) for other in entries[1:]]
        else:
            pairs = [
                (entries[x], entries[y])
                for x in range(len(entries))
                for y in range(x + 1, len(entries))
            ]
        for (pa, ia), (pb, ib) in pairs:
            a, b = items[pa], items[pb]
            if pa == pb and abs(ia - ib) < k:
                continue
            if pa != pb and tier_hash(a, "constants") == tier_hash(b, "constants"):
                continue
            ta, tb = a.tokens, b.tokens
            start_a, start_b, length = _maximal_run(
                ta, ia, tb, ib, same_function=pa == pb
            )
            if length < k or (pa, start_a, pb, start_b) in seen:
                continue
            seen.add((pa, start_a, pb, start_b))
            if _covered_by_near_match(ta, start_a, tb, start_b, length, near_threshold):
                continue
            segment = ta[start_a : start_a + length]
            if sum(token.weight for token in segment) == 0:
                continue
            content = _run_hash(ta, start_a, length)
            runs[content][(pa, start_a, start_a + length)] = None
            runs[content][(pb, start_b, start_b + length)] = None
            run_tokens.setdefault(content, segment)

    groups: list[BlockCloneGroup] = []
    covered: dict[int, list[tuple[int, int]]] = defaultdict(list)
    ordered = sorted(
        runs.items(), key=lambda item: (-len(run_tokens[item[0]]), item[0])
    )
    for content, spans in ordered:
        span_list = sorted(spans)
        if all(
            any(start >= s and end <= e for s, e in covered[index])
            for index, start, end in span_list
        ):
            continue
        for index, start, end in span_list:
            covered[index].append((start, end))
        occurrences = []
        for index, start, end in span_list:
            profile = items[index]
            segment = profile.tokens[start:end]
            occurrences.append(
                BlockOccurrence(
                    path=profile.occurrence.path,
                    lineno=segment[0].lineno,
                    end_lineno=max(token.end_lineno for token in segment),
                    qualname=profile.occurrence.qualname,
                    start_index=start,
                    end_index=end,
                )
            )
        stmt_count = len(run_tokens[content])
        groups.append(
            BlockCloneGroup(
                hash=content,
                stmt_count=stmt_count,
                count=len(occurrences),
                occurrences=occurrences,
                savings=stmt_count * (len(occurrences) - 1),
                summary=_run_summary(run_tokens[content]),
            )
        )
    groups.sort(key=lambda g: (-g.savings, -g.count, g.hash))
    return groups


def suppress_covered_blocks(
    blocks: list[BlockCloneGroup], near_rows: list[SimilarityResult]
) -> list[BlockCloneGroup]:
    """Drop repeated blocks whose functions are already reported as near matches.

    A block shared by exactly the functions of a near-match pair adds no
    information beyond that pair, so combined reports hide it.
    """

    paired: set[frozenset[tuple[str, str]]] = set()
    for row in near_rows:
        paired.add(
            frozenset({(row.a.path, row.a.qualname), (row.b.path, row.b.qualname)})
        )
    kept = []
    for block in blocks:
        functions = {(occ.path, occ.qualname) for occ in block.occurrences}
        if len(functions) == 2 and frozenset(functions) in paired:
            continue
        kept.append(block)
    return kept
