"""Tests for pydry.engine: exact tiers, near-match ranking, clusters, and blocks."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from pydry.engine import (
    _pattern_labels,
    _suggest_refactor,
    block_clones,
    cluster_near_matches,
    exact_groups,
    near_matches,
    scan_functions,
)
from pydry.models import SimilarityEvidence

LOADER_A = """
def load_users(path):
    with open(path) as fh:
        raw = fh.read()
    rows = raw.splitlines()
    out = []
    for row in rows:
        if not row.strip():
            continue
        parts = row.split(",")
        rec = {"id": int(parts[0]), "name": parts[1].strip()}
        out.append(rec)
    out.sort(key=lambda r: r["id"])
    return out
"""

LOADER_B = """
def load_orders(path):
    with open(path) as fh:
        raw = fh.read()
    rows = raw.splitlines()
    out = []
    seen = set()
    for row in rows:
        if not row.strip():
            continue
        parts = row.split("\\t")
        rec = {"id": int(parts[0]), "sku": parts[1].strip()}
        if rec["id"] in seen:
            continue
        seen.add(rec["id"])
        out.append(rec)
    out.sort(key=lambda r: r["id"])
    return out
"""

BOILERPLATE = """
class Point:
    def __init__(self, x, y):
        self._x = x
        self._y = y

    @property
    def x(self):
        return self._x

    @property
    def y(self):
        return self._y


class Size:
    def __init__(self, w, h):
        self._w = w
        self._h = h

    @property
    def w(self):
        return self._w

    def stub(self):
        raise NotImplementedError


class Other:
    def stub(self):
        raise NotImplementedError
"""


LOADER_C = LOADER_A.replace("load_users", "load_people").replace(
    "    rows = raw.splitlines()\n",
    "    rows = raw.splitlines()\n    log(path)\n",
)


def _evidence(**overrides: object) -> SimilarityEvidence:
    values: dict[str, object] = {
        "shared_statements": 6,
        "a_statements": 6,
        "b_statements": 6,
        "identical_statements": 6,
        "renamed_statements": 0,
        "constant_statements": 0,
        "only_in_a": 0,
        "only_in_b": 0,
        "call_similarity": 1.0,
        "parameter_statements": 0,
    }
    values.update(overrides)
    return SimilarityEvidence(**values)  # type: ignore[arg-type]


class RepoMixin:
    def _make_repo(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)  # type: ignore[attr-defined]
        root = Path(tmp.name)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content))
        return root


class PatternLabelTests(unittest.TestCase):
    def _features(self, calls: dict[str, int]) -> dict[str, object]:
        from collections import Counter

        return {"call_names": Counter(calls)}

    def test_renamed_locals_only(self) -> None:
        labels = _pattern_labels(
            _evidence(identical_statements=4, renamed_statements=2),
            self._features({"f": 1}),
            self._features({"f": 1}),
        )
        self.assertEqual(labels, ["renamed_locals"])

    def test_literal_specialization(self) -> None:
        labels = _pattern_labels(
            _evidence(identical_statements=4, constant_statements=2),
            self._features({"f": 1}),
            self._features({"f": 1}),
        )
        self.assertEqual(labels, ["literal_specialization"])

    def test_structural_and_mixed_variation(self) -> None:
        labels = _pattern_labels(
            _evidence(
                shared_statements=5,
                b_statements=8,
                only_in_b=3,
                constant_statements=1,
                identical_statements=4,
            ),
            self._features({"f": 1}),
            self._features({"f": 1}),
        )
        self.assertEqual(labels, ["structural_variant", "mixed_variation"])

    def test_different_dependencies_requires_calls_on_both_sides(self) -> None:
        evidence = _evidence(call_similarity=0.0)
        self.assertEqual(
            _pattern_labels(
                evidence, self._features({"f": 1}), self._features({"g": 1})
            ),
            ["different_dependencies"],
        )
        self.assertEqual(
            _pattern_labels(evidence, self._features({}), self._features({})), []
        )


class SuggestRefactorTests(unittest.TestCase):
    def test_async_or_generator_differences_leave_separate(self) -> None:
        self.assertEqual(
            _suggest_refactor([], ["async_boundary_diff"], _evidence()),
            "leave_separate",
        )
        self.assertEqual(
            _suggest_refactor(["renamed_locals"], ["return_shape_diff"], _evidence()),
            "leave_separate",
        )

    def test_label_driven_suggestions(self) -> None:
        cases = {
            ("renamed_locals",): "remove_duplicate",
            ("parameter_specialization",): "delegate_to_general_form",
            ("literal_specialization",): "parameterize_constant",
            ("different_dependencies",): "inject_dependency",
        }
        for labels, expected in cases.items():
            with self.subTest(labels=labels):
                self.assertEqual(
                    _suggest_refactor(list(labels), [], _evidence()), expected
                )

    def test_structural_variant_depends_on_divergence(self) -> None:
        small = _evidence(
            shared_statements=10, a_statements=10, b_statements=12, only_in_b=2
        )
        large = _evidence(
            shared_statements=6,
            a_statements=10,
            b_statements=10,
            only_in_a=4,
            only_in_b=4,
        )
        self.assertEqual(
            _suggest_refactor(["structural_variant"], [], small),
            "extract_common_helper",
        )
        self.assertEqual(
            _suggest_refactor(["structural_variant"], [], large), "extract_shared_steps"
        )


class ExactGroupTests(RepoMixin, unittest.TestCase):
    def test_tier_reports_tightest_equivalence(self) -> None:
        root = self._make_repo(
            {
                "a.py": """
                def one(items):
                    total = 0
                    for item in items:
                        total += item * 2
                    return total

                def two(values):
                    acc = 0
                    for value in values:
                        acc += value * 2
                    return acc

                def three(values):
                    acc = 0
                    for value in values:
                        acc += value * 3
                    return acc

                def four(items):
                    total = 0
                    for item in items:
                        total += item * 2
                    return total
                """
            }
        )
        groups = exact_groups(root)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group.tier, "constants")
        self.assertEqual(group.count, 4)
        self.assertEqual(group.stmt_count, 4)
        self.assertEqual(group.savings, 12)

        renamed_only = exact_groups(root, normalize_constants=False)
        self.assertEqual(len(renamed_only), 1)
        self.assertEqual(renamed_only[0].tier, "renamed")
        self.assertEqual(renamed_only[0].count, 3)

        identical_only = exact_groups(
            root, normalize_constants=False, normalize_local_names=False
        )
        self.assertEqual(len(identical_only), 1)
        self.assertEqual(identical_only[0].tier, "identical")
        self.assertEqual(
            [o.name for o in identical_only[0].occurrences], ["one", "four"]
        )

    def test_different_helpers_are_not_renamed_duplicates(self) -> None:
        root = self._make_repo(
            {
                "a.py": """
                def shape(a, b):
                    left = prepare(a)
                    return jaccard(left, b)

                def order(a, b):
                    left = prepare(a)
                    return lcs_ratio(left, b)

                def shape_again(x, y):
                    lhs = prepare(x)
                    return jaccard(lhs, y)
                """
            }
        )
        groups = exact_groups(root)
        self.assertEqual(len(groups), 1)
        self.assertEqual(
            [o.name for o in groups[0].occurrences], ["shape", "shape_again"]
        )
        self.assertEqual(groups[0].tier, "renamed")

    def test_trivial_and_short_functions_are_skipped_by_default(self) -> None:
        root = self._make_repo({"a.py": BOILERPLATE})
        self.assertEqual(exact_groups(root), [])
        groups = exact_groups(root, ignore_trivial=False, min_statements=1)
        self.assertEqual(len(groups), 1)
        self.assertEqual(
            [o.qualname for o in groups[0].occurrences], ["Size.stub", "Other.stub"]
        )

    def test_include_canonical_and_min_count(self) -> None:
        root = self._make_repo(
            {"a.py": LOADER_A + LOADER_A.replace("load_users", "copy")}
        )
        groups = exact_groups(root, include_canonical=True)
        self.assertEqual(len(groups), 1)
        self.assertIn("FunctionDef", groups[0].canonical or "")
        self.assertEqual(exact_groups(root, min_count=3), [])
        with self.assertRaises(ValueError):
            exact_groups(root, min_count=1)


class NearMatchTests(RepoMixin, unittest.TestCase):
    def test_real_variant_outranks_boilerplate(self) -> None:
        root = self._make_repo({"a.py": LOADER_A, "b.py": LOADER_B + BOILERPLATE})
        rows = near_matches(root, threshold=0.7)
        self.assertEqual(len(rows), 1)
        top = rows[0]
        self.assertEqual({top.a.name, top.b.name}, {"load_users", "load_orders"})
        self.assertEqual(top.shared_statements, 12)
        self.assertEqual(top.evidence.only_in_b, 4)
        self.assertEqual(top.evidence.constant_statements, 2)
        self.assertIn("structural_variant", top.pattern_labels)
        self.assertIn("4 statement(s) only in load_orders", top.key_differences)
        self.assertEqual(top.suggested_refactor_kind, "extract_common_helper")
        self.assertAlmostEqual(top.priority, 12 * top.refactorability_score, places=1)
        self.assertEqual(top.cluster_id, 1)
        self.assertIn("fingerprint", top.metadata)

    def test_exact_duplicates_are_not_repeated_as_near_matches(self) -> None:
        root = self._make_repo(
            {"a.py": LOADER_A, "b.py": LOADER_A.replace("load_users", "copy")}
        )
        self.assertEqual(near_matches(root, threshold=0.5), [])

    def test_parameter_specialization_is_found_by_loose_alignment(self) -> None:
        root = self._make_repo(
            {
                "a.py": """
                def clamp_value(value, low, high):
                    if value < low:
                        return low
                    if value > high:
                        return high
                    return value

                def clamp_to_unit(value):
                    if value < 0.0:
                        return 0.0
                    if value > 1.0:
                        return 1.0
                    return value
                """
            }
        )
        rows = near_matches(root)
        self.assertEqual(len(rows), 1)
        self.assertIn("parameter_specialization", rows[0].pattern_labels)
        self.assertEqual(rows[0].suggested_refactor_kind, "delegate_to_general_form")
        self.assertEqual(rows[0].evidence.parameter_statements, 4)

    def test_async_variant_is_left_separate(self) -> None:
        root = self._make_repo(
            {
                "a.py": """
                def fetch(client, key):
                    resp = client.get(key)
                    data = resp.json()
                    if "error" in data:
                        raise RuntimeError(data["error"])
                    return data

                async def fetch_async(client, key):
                    resp = await client.get(key)
                    data = await resp.json()
                    if "error" in data:
                        raise RuntimeError(data["error"])
                    return data
                """
            }
        )
        rows = near_matches(root, threshold=0.5)
        self.assertEqual(len(rows), 1)
        self.assertIn("async_boundary_diff", rows[0].risk_flags)
        self.assertEqual(rows[0].suggested_refactor_kind, "leave_separate")

    def test_clusters_join_transitive_pairs(self) -> None:
        root = self._make_repo({"a.py": LOADER_A, "b.py": LOADER_B, "c.py": LOADER_C})
        rows = near_matches(root, threshold=0.7)
        self.assertEqual(len(rows), 3)
        self.assertEqual({row.cluster_id for row in rows}, {1})
        clusters = cluster_near_matches(rows)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0].members), 3)
        self.assertEqual(clusters[0].pair_count, 3)
        self.assertEqual(clusters[0].savings, 2 * clusters[0].shared_statements)

    def test_threshold_and_top_k_validation(self) -> None:
        root = self._make_repo({"a.py": LOADER_A})
        with self.assertRaises(ValueError):
            near_matches(root, threshold=1.5)
        with self.assertRaises(ValueError):
            near_matches(root, top_k=-1)
        self.assertEqual(near_matches(root, top_k=0), [])

    def test_top_k_is_a_prefix_of_the_full_ranking(self) -> None:
        root = self._make_repo({"a.py": LOADER_A, "b.py": LOADER_B, "c.py": LOADER_C})
        full = near_matches(root, threshold=0.7)
        limited = near_matches(root, threshold=0.7, top_k=2)
        self.assertEqual(
            [(r.a.name, r.b.name) for r in limited],
            [(r.a.name, r.b.name) for r in full[:2]],
        )


class BlockCloneTests(RepoMixin, unittest.TestCase):
    PIPELINE = """
    def big_pipeline(path):
        with open(path) as fh:
            raw = fh.read()
        rows = raw.splitlines()
        out = []
        for row in rows:
            if not row.strip():
                continue
            parts = row.split(",")
            rec = {"id": int(parts[0]), "name": parts[1].strip()}
            out.append(rec)
        out.sort(key=lambda r: r["id"])
        stats = {}
        for rec in out:
            stats[rec["id"]] = len(rec["name"])
        keys = sorted(stats)
        hist = {}
        for k in keys:
            v = stats[k]
            hist[v] = hist.get(v, 0) + 1
        best = None
        for v, c in hist.items():
            if best is None or c > best[1]:
                best = (v, c)
        report = []
        for k in keys:
            report.append(f"{k}: {stats[k]}")
        if best is not None:
            report.append(f"mode: {best[0]}")
        text = "\\n".join(report)
        print(text)
        summary = {"count": len(out), "mode": best}
        if summary["count"] == 0:
            summary["empty"] = True
        return summary
    """

    def test_block_copied_into_larger_function_is_found(self) -> None:
        root = self._make_repo({"a.py": LOADER_A, "b.py": self.PIPELINE})
        groups = block_clones(root)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group.count, 2)
        self.assertEqual(group.stmt_count, 11)
        self.assertEqual(group.savings, 11)
        names = sorted(o.qualname for o in group.occurrences)
        self.assertEqual(names, ["big_pipeline", "load_users"])
        pipeline = next(o for o in group.occurrences if o.qualname == "big_pipeline")
        self.assertEqual((pipeline.lineno, pipeline.end_lineno), (3, 13))

    def test_whole_function_duplicates_are_left_to_exact_groups(self) -> None:
        root = self._make_repo(
            {"a.py": LOADER_A, "b.py": LOADER_A.replace("load_users", "copy")}
        )
        self.assertEqual(block_clones(root), [])

    def test_min_statements_controls_sensitivity(self) -> None:
        root = self._make_repo({"a.py": LOADER_A, "b.py": self.PIPELINE})
        self.assertEqual(block_clones(root, min_statements=12), [])
        with self.assertRaises(ValueError):
            block_clones(root, min_statements=1)

    def test_repeat_inside_one_function(self) -> None:
        body = """
        def twice(a, b):
            x = prep(a)
            y = prep(b)
            z = combine(x, y)
            log(z)
            store(z)
            first = z
            x = prep(a)
            y = prep(b)
            z = combine(x, y)
            log(z)
            store(z)
            return first, z
        """
        root = self._make_repo({"a.py": body})
        groups = block_clones(root, min_statements=5)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].count, 2)
        self.assertEqual(groups[0].stmt_count, 5)


class CandidateFilterTests(unittest.TestCase):
    def test_prefix_filter_matches_brute_force_on_demo(self) -> None:
        from pydry.engine import _candidate_pairs, _compare

        demo = Path(__file__).resolve().parent.parent / "demo"
        candidates = sorted(
            (
                p
                for p in scan_functions(demo)
                if p.eligible(min_statements=2, ignore_trivial=True)
            ),
            key=lambda p: p.stmt_count,
        )
        for threshold in (0.5, 0.8, 1.0):
            with self.subTest(threshold=threshold):
                brute = set()
                for i, a in enumerate(candidates):
                    for b in candidates[i + 1 :]:
                        if _compare(a, b, threshold=threshold, plugin_errors=None):
                            brute.add((a.occurrence.qualname, b.occurrence.qualname))
                filtered = set()
                for a, b in _candidate_pairs(candidates, threshold):
                    if _compare(a, b, threshold=threshold, plugin_errors=None):
                        filtered.add((a.occurrence.qualname, b.occurrence.qualname))
                self.assertEqual(filtered, brute)
                self.assertTrue(brute)


class ScanTests(RepoMixin, unittest.TestCase):
    def test_scan_errors_are_collected_or_raised(self) -> None:
        root = self._make_repo({"bad.py": "def broken(:\n", "ok.py": LOADER_A})
        errors: list[str] = []
        profiles = scan_functions(root, scan_errors=errors)
        self.assertEqual([p.occurrence.name for p in profiles], ["load_users"])
        self.assertEqual(len(errors), 1)
        with self.assertRaises(RuntimeError):
            scan_functions(root, strict=True)

    def test_profiles_can_be_shared_between_analyses(self) -> None:
        root = self._make_repo({"a.py": LOADER_A, "b.py": LOADER_B})
        profiles = scan_functions(root)
        self.assertEqual(exact_groups(root, profiles=profiles), [])
        self.assertEqual(len(near_matches(root, threshold=0.7, profiles=profiles)), 1)


if __name__ == "__main__":
    unittest.main()


class BlockDetectionEdgeTests(RepoMixin, unittest.TestCase):
    """Bucket cap, runs across compound boundaries, and coverage suppression."""

    def test_seed_in_the_middle_extends_backwards_to_the_maximal_run(self) -> None:
        # Both functions share an 8-statement run; every 6-window inside it
        # seeds the same maximal run, which must be reported exactly once.
        body = """
        def one(a):
            x = prep(a)
            y = prep(x)
            z = prep(y)
            if z:
                log(z)
                store(z)
            w = wrap(z)
            emit(w)
            return w

        def two(b):
            b = warm(b)
            x = prep(b)
            y = prep(x)
            z = prep(y)
            if z:
                log(z)
                store(z)
            w = wrap(z)
            emit(w)
            return finish(w)
        """
        root = self._make_repo({"a.py": body})
        groups = block_clones(root, min_statements=6, near_threshold=0.95)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].stmt_count, 8)
        starts = sorted(o.start_index for o in groups[0].occurrences)
        self.assertEqual(starts, [0, 1])

    def test_run_can_cross_a_compound_statement_boundary(self) -> None:
        body = """
        def first(items):
            for item in items:
                check(item)
                tally(item)
            flush()
            report()
            close()
            return done()

        def second(rows):
            setup()
            for row in rows:
                check(row)
                tally(row)
            flush()
            report()
            close()
            return other()
        """
        root = self._make_repo({"a.py": body})
        groups = block_clones(root, min_statements=6, near_threshold=0.95)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].stmt_count, 6)
        first = next(o for o in groups[0].occurrences if o.qualname == "first")
        self.assertEqual((first.start_index, first.end_index), (0, 6))
        self.assertEqual((first.lineno, first.end_lineno), (3, 8))

    def test_coverage_rule_follows_the_near_threshold(self) -> None:
        # 7 of 8 statements shared: coverage 0.875 on both sides.
        body = """
        def alpha(a):
            x = prep(a)
            y = prep(x)
            z = prep(y)
            log(z)
            store(z)
            w = wrap(z)
            emit(w)
            return w

        def beta(b):
            x = prep(b)
            y = prep(x)
            z = prep(y)
            log(z)
            store(z)
            w = wrap(z)
            emit(w)
            return finish(w)
        """
        root = self._make_repo({"a.py": body})
        self.assertEqual(block_clones(root, min_statements=5, near_threshold=0.8), [])
        kept = block_clones(root, min_statements=5, near_threshold=0.9)
        self.assertEqual([g.stmt_count for g in kept], [7])
        # Zero disables the rule: the whole-function run is reported as a block.
        self.assertEqual(
            [
                g.stmt_count
                for g in block_clones(root, min_statements=5, near_threshold=0.0)
            ],
            [7],
        )
        with self.assertRaises(ValueError):
            block_clones(root, near_threshold=1.5)

    def test_runs_at_different_depths_are_not_left_to_near_matches(self) -> None:
        # The same six calls, once at the top level and once under an ``if``.
        # Near matching compares absolute depth and will not align them, so
        # the block must survive the coverage rule.
        body = """
        def flat(x):
            a = prep(x)
            b = prep(a)
            c = prep(b)
            log(c)
            store(c)
            emit(c)

        def guarded(x):
            if x:
                a = prep(x)
                b = prep(a)
                c = prep(b)
                log(c)
                store(c)
                emit(c)
        """
        root = self._make_repo({"a.py": body})
        self.assertEqual(near_matches(root, threshold=0.8), [])
        groups = block_clones(root, min_statements=6, near_threshold=0.8)
        self.assertEqual([g.stmt_count for g in groups], [6])

    def test_bucket_cap_extends_against_the_first_occurrence_only(self) -> None:
        from pydry import blocks

        template = """
        def fn{n}(a):
            x = prep(a)
            y = prep(x)
            z = prep(y)
            log(z)
            store(z)
            emit(z)
            {tail}
        """
        files = {}
        for n in range(6):
            tail = "return z" if n < 3 else "return finish(z)\n    return z"
            files[f"m{n}.py"] = template.format(n=n, tail=tail)
        root = self._make_repo(files)
        original = blocks._MAX_BUCKET_PAIRS
        blocks._MAX_BUCKET_PAIRS = 2
        try:
            capped = block_clones(root, min_statements=6, near_threshold=1.0)
        finally:
            blocks._MAX_BUCKET_PAIRS = original
        full = block_clones(root, min_statements=6, near_threshold=1.0)
        # Every occurrence is still reported under the cap; only the grouping
        # by maximal run may differ from the pairwise result.
        capped_occurrences = {
            (o.qualname, o.start_index) for g in capped for o in g.occurrences
        }
        full_occurrences = {
            (o.qualname, o.start_index) for g in full for o in g.occurrences
        }
        self.assertEqual(capped_occurrences, full_occurrences)
        self.assertGreaterEqual(len(capped), len(full))

    def test_suppress_covered_blocks_only_hides_two_function_blocks(self) -> None:
        from pydry.engine import suppress_covered_blocks
        from pydry.models import BlockCloneGroup, BlockOccurrence

        def occ(path: str, name: str) -> BlockOccurrence:
            return BlockOccurrence(path, 1, 5, name, 0, 5)

        pair_block = BlockCloneGroup(
            "h1", 5, 2, [occ("a.py", "f"), occ("b.py", "g")], 5, ""
        )
        triple_block = BlockCloneGroup(
            "h2", 5, 3, [occ("a.py", "f"), occ("b.py", "g"), occ("c.py", "h")], 10, ""
        )
        other_block = BlockCloneGroup(
            "h3", 5, 2, [occ("a.py", "f"), occ("c.py", "h")], 5, ""
        )
        root = self._make_repo({"a.py": LOADER_A, "b.py": LOADER_B})
        near = near_matches(root, threshold=0.7)
        near[0].a = near[0].a.__class__(
            **{**near[0].a.__dict__, "path": "a.py", "qualname": "f"}
        )
        near[0].b = near[0].b.__class__(
            **{**near[0].b.__dict__, "path": "b.py", "qualname": "g"}
        )
        kept = suppress_covered_blocks([pair_block, triple_block, other_block], near)
        self.assertEqual([g.hash for g in kept], ["h2", "h3"])


class RemainingBranchTests(RepoMixin, unittest.TestCase):
    def test_pure_assignment_runs_and_short_period_repeats_are_ignored(self) -> None:
        body = """
        def constants():
            a = 1
            b = 2
            c = 3
            d = 4
            e = 5
            f = 6
            return g(a, b, c, d, e, f)

        def constants_again():
            a = 1
            b = 2
            c = 3
            d = 4
            e = 5
            f = 6
            return h(a, b, c, d, e, f)

        def periodic(x):
            x = step(x)
            x = step(x)
            x = step(x)
            x = step(x)
            x = step(x)
            x = step(x)
            x = step(x)
            x = step(x)
            return x
        """
        root = self._make_repo({"a.py": body})
        self.assertEqual(block_clones(root, min_statements=6, near_threshold=1.0), [])

    def test_identical_bodies_with_different_signatures_fall_back_to_helper(
        self,
    ) -> None:
        root = self._make_repo(
            {
                "a.py": """
                def one(*items):
                    total = start()
                    for item in items:
                        total = combine(total, item)
                    return total

                def two(items, **options):
                    total = start()
                    for item in items:
                        total = combine(total, item)
                    return total
                """
            }
        )
        rows = near_matches(root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].pattern_labels, [])
        self.assertEqual(rows[0].suggested_refactor_kind, "extract_common_helper")
        self.assertIn("parameter count differs (1 vs 2)", rows[0].key_differences)

    def test_abstract_candidates_top_k_and_jsonable_sets(self) -> None:
        from pydry.engine import abstract_candidates, to_jsonable

        root = self._make_repo({"a.py": LOADER_A, "b.py": LOADER_B, "c.py": LOADER_C})
        self.assertEqual(len(abstract_candidates(root, threshold=0.7, top_k=1)), 1)
        self.assertEqual(
            to_jsonable({"names": frozenset({"b", "a"})}), {"names": ["a", "b"]}
        )


class ClosureIdentityTests(RepoMixin, unittest.TestCase):
    def test_closure_and_parameter_references_are_not_exact_duplicates(self) -> None:
        root = self._make_repo(
            {
                "closures.py": """
                def first(x):
                    def inner(y):
                        return x + y
                    value = inner(1)
                    return value

                def second(x):
                    def inner(y):
                        return y + y
                    value = inner(1)
                    return value
                """
            }
        )
        self.assertEqual(exact_groups(root), [])


class BlockLocationAndFilterTests(RepoMixin, unittest.TestCase):
    def test_blocks_starting_at_a_case_header_report_real_lines(self) -> None:
        body = """
        def first(cmd):
            match cmd:
                case ["go", where]:
                    a = prep(where)
                    b = prep(a)
                    log(b)
                    store(b)
                    emit(b)
                    return b
            return None

        def second(cmd):
            setup()
            match cmd:
                case ["go", where]:
                    a = prep(where)
                    b = prep(a)
                    log(b)
                    store(b)
                    emit(b)
                    return finish(b)
            return None
        """
        root = self._make_repo({"a.py": body})
        groups = block_clones(root, min_statements=6, near_threshold=1.0)
        self.assertEqual(len(groups), 1)
        for occurrence in groups[0].occurrences:
            self.assertGreater(occurrence.lineno, 0)
            self.assertLessEqual(occurrence.lineno, occurrence.end_lineno)
        # The maximal run includes the ``match`` header on line 3 and ends at
        # ``emit`` on line 9, before the returns diverge.
        first = next(o for o in groups[0].occurrences if o.qualname == "first")
        self.assertEqual((first.lineno, first.end_lineno), (3, 9))

    def test_function_size_filter_applies_to_blocks(self) -> None:
        root = self._make_repo({"a.py": LOADER_A, "b.py": BlockCloneTests.PIPELINE})
        self.assertEqual(len(block_clones(root)), 1)
        self.assertEqual(block_clones(root, min_function_statements=1000), [])
