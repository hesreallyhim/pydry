"""Integration tests against the demo/ corpus.

These run the full engine over demo/ and assert the findings a user would
see: exact groups with tiers, near-match labels and suggestions, risk flags,
and repeated blocks.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from pydry.engine import (
    block_clones,
    exact_groups,
    near_matches,
    scan_functions,
    suppress_covered_blocks,
)

DEMO = Path(__file__).resolve().parent.parent / "demo"


def _pair(rows, first, second):  # type: ignore[no-untyped-def]
    for row in rows:
        if {row.a.name, row.b.name} == {first, second}:
            return row
    return None


class TestExactDuplicates(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profiles = scan_functions(DEMO)
        cls.groups = exact_groups(DEMO, profiles=cls.profiles)
        cls.by_names = {frozenset(o.name for o in g.occurrences): g for g in cls.groups}

    def test_expected_groups_and_tiers(self) -> None:
        expected = {
            frozenset(
                {"process_csv_row", "process_json_entry", "clean_record"}
            ): "renamed",
            frozenset({"load_and_transform", "read_and_convert"}): "renamed",
            frozenset({"sum_positive", "add_positive_numbers"}): "renamed",
            frozenset({"to_dict"}): "renamed",
            frozenset({"build_user_query", "build_admin_query"}): "constants",
            frozenset({"format_error_message", "format_warning_message"}): "constants",
        }
        self.assertEqual(set(self.by_names), set(expected))
        for names, tier in expected.items():
            self.assertEqual(self.by_names[names].tier, tier, names)

    def test_groups_are_ranked_by_savings(self) -> None:
        savings = [g.savings for g in self.groups]
        self.assertEqual(savings, sorted(savings, reverse=True))
        self.assertEqual(self.groups[0].count, 3)
        self.assertEqual(self.groups[0].savings, 16)

    def test_disabling_constant_normalization_drops_constant_tier(self) -> None:
        groups = exact_groups(DEMO, normalize_constants=False, profiles=self.profiles)
        self.assertEqual(len(groups), 4)
        self.assertTrue(all(g.tier == "renamed" for g in groups))

    def test_one_line_partial_application_helpers_are_below_min_statements(
        self,
    ) -> None:
        names = {o.name for g in self.groups for o in g.occurrences}
        self.assertNotIn("make_prefix_formatter", names)
        loose = exact_groups(DEMO, min_statements=1, profiles=self.profiles)
        self.assertEqual(len(loose), len(self.groups))


class TestNearMatches(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profiles = scan_functions(DEMO)
        cls.default = near_matches(DEMO, profiles=cls.profiles)
        cls.loose = near_matches(DEMO, threshold=0.45, profiles=cls.profiles)

    def test_default_threshold_reports_credible_pairs_only(self) -> None:
        pairs = {frozenset({r.a.name, r.b.name}) for r in self.default}
        self.assertEqual(
            pairs,
            {
                frozenset({"retry", "retry_quickly"}),
                frozenset({"clamp_value", "clamp_to_unit"}),
                frozenset({"collect_even", "filter_active_users"}),
            },
        )

    def test_results_sorted_by_priority(self) -> None:
        priorities = [r.priority for r in self.default]
        self.assertEqual(priorities, sorted(priorities, reverse=True))

    def test_exact_duplicates_are_not_repeated(self) -> None:
        self.assertIsNone(_pair(self.loose, "process_csv_row", "process_json_entry"))
        self.assertIsNone(_pair(self.loose, "paginate_results", "paginate_logs"))

    def test_parameter_specialization_suggests_delegation(self) -> None:
        for first, second in (
            ("clamp_value", "clamp_to_unit"),
            ("retry", "retry_quickly"),
        ):
            pair = _pair(self.default, first, second)
            self.assertIsNotNone(pair, (first, second))
            assert pair is not None
            self.assertIn("parameter_specialization", pair.pattern_labels)
            self.assertEqual(pair.suggested_refactor_kind, "delegate_to_general_form")
            self.assertGreater(pair.evidence.parameter_statements, 0)

    def test_structural_variant_reports_inserted_statements(self) -> None:
        pair = _pair(self.loose, "filter_active_users", "filter_active_admins")
        self.assertIsNotNone(pair)
        assert pair is not None
        self.assertIn("structural_variant", pair.pattern_labels)
        self.assertEqual(pair.evidence.only_in_b + pair.evidence.only_in_a, 4)
        self.assertEqual(pair.suggested_refactor_kind, "extract_common_helper")

    def test_exception_behavior_difference_is_a_risk(self) -> None:
        pair = _pair(self.loose, "parse_int_strict", "parse_int_lenient")
        self.assertIsNotNone(pair)
        assert pair is not None
        self.assertIn("exception_behavior_diff", pair.risk_flags)
        self.assertLess(pair.refactorability_score, pair.similarity_score)

    def test_side_effects_risk_flag(self) -> None:
        pair = _pair(self.loose, "save_report_txt", "save_report_csv")
        self.assertIsNotNone(pair)
        assert pair is not None
        self.assertIn("possible_side_effects", pair.risk_flags)

    def test_async_and_generator_variants_are_left_separate(self) -> None:
        for first, second in (
            ("fetch_user", "fetch_user_async"),
            ("collect_even", "iter_even"),
        ):
            pair = _pair(self.loose, first, second)
            self.assertIsNotNone(pair, (first, second))
            assert pair is not None
            self.assertEqual(pair.suggested_refactor_kind, "leave_separate")

    def test_every_pair_has_a_cluster_and_fingerprint(self) -> None:
        for row in self.loose:
            self.assertIsNotNone(row.cluster_id)
            self.assertTrue(row.metadata.get("fingerprint"))


class TestBlockClones(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profiles = scan_functions(DEMO)
        cls.blocks = block_clones(DEMO, profiles=cls.profiles)

    def test_parsing_block_copied_into_pipeline_is_found(self) -> None:
        block = next(
            b
            for b in self.blocks
            if {o.qualname for o in b.occurrences}
            == {"parse_records", "summarize_file"}
        )
        self.assertEqual(block.stmt_count, 11)
        self.assertEqual(block.savings, 11)
        for occurrence in block.occurrences:
            self.assertGreater(occurrence.end_lineno, occurrence.lineno)

    def test_blocks_covered_by_near_matches_are_suppressed_in_reports(self) -> None:
        near = near_matches(DEMO, profiles=self.profiles)
        names = {frozenset(o.qualname for o in b.occurrences) for b in self.blocks}
        self.assertIn(frozenset({"retry", "retry_quickly"}), names)
        kept = suppress_covered_blocks(self.blocks, near)
        kept_names = {frozenset(o.qualname for o in b.occurrences) for b in kept}
        self.assertNotIn(frozenset({"retry", "retry_quickly"}), kept_names)
        self.assertIn(frozenset({"parse_records", "summarize_file"}), kept_names)


if __name__ == "__main__":
    unittest.main()
