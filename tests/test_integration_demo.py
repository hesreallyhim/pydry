"""Integration / regression tests using the demo corpus.

These tests run the full engine against demo/ and assert expected
detection behavior. They serve as a regression baseline — if a change
to the engine breaks one of these tests, it means detection behavior
shifted and should be reviewed intentionally.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from pydry.engine import abstract_candidates, exact_groups, near_matches

DEMO_ROOT = Path(__file__).resolve().parent.parent / "demo"


def _names(group) -> set[str]:
    """Extract the set of function qualnames from an ExactGroup."""
    return {occ.qualname for occ in group.occurrences}


def _pair_key(result) -> tuple[str, str]:
    """Return a sorted (qualname, qualname) pair for a SimilarityResult."""
    a, b = result.a.qualname, result.b.qualname
    return (a, b) if a < b else (b, a)


class TestExactDuplicates(unittest.TestCase):
    """Regression tests for pydry exact mode against the demo corpus."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.groups_default = exact_groups(DEMO_ROOT, min_count=2)
        cls.groups_normalized = exact_groups(
            DEMO_ROOT,
            min_count=2,
            normalize_local_names=True,
            normalize_constants=True,
        )

    def test_normalized_finds_at_least_seven_groups(self) -> None:
        self.assertGreaterEqual(len(self.groups_normalized), 7)

    def test_cross_file_exact_triple(self) -> None:
        """process_csv_row, process_json_entry, clean_record are exact dupes."""
        expected = {"process_csv_row", "process_json_entry", "clean_record"}
        found = any(expected <= _names(g) for g in self.groups_normalized)
        self.assertTrue(found, f"Expected group {expected} not found")

    def test_class_method_exact_pair(self) -> None:
        """UserSerializer.to_dict and OrderSerializer.to_dict are exact dupes."""
        expected = {"UserSerializer.to_dict", "OrderSerializer.to_dict"}
        found = any(expected <= _names(g) for g in self.groups_normalized)
        self.assertTrue(found, f"Expected group {expected} not found")

    def test_local_name_normalization(self) -> None:
        """sum_positive and add_positive_numbers match with name normalization."""
        expected = {"sum_positive", "add_positive_numbers"}
        found = any(expected <= _names(g) for g in self.groups_normalized)
        self.assertTrue(found, f"Expected group {expected} not found")

    def test_constant_normalization(self) -> None:
        """paginate_results and paginate_logs match with constant normalization."""
        expected = {"paginate_results", "paginate_logs"}
        found = any(expected <= _names(g) for g in self.groups_normalized)
        self.assertTrue(found, f"Expected group {expected} not found")

    def test_query_builders_match_normalized(self) -> None:
        """build_user_query and build_admin_query match with normalization."""
        expected = {"build_user_query", "build_admin_query"}
        found = any(expected <= _names(g) for g in self.groups_normalized)
        self.assertTrue(found, f"Expected group {expected} not found")

    def test_format_messages_match_normalized(self) -> None:
        """format_error_message and format_warning_message match with normalization."""
        expected = {"format_error_message", "format_warning_message"}
        found = any(expected <= _names(g) for g in self.groups_normalized)
        self.assertTrue(found, f"Expected group {expected} not found")

    def test_loaders_match_normalized(self) -> None:
        """load_and_transform and read_and_convert match with normalization."""
        expected = {"load_and_transform", "read_and_convert"}
        found = any(expected <= _names(g) for g in self.groups_normalized)
        self.assertTrue(found, f"Expected group {expected} not found")

    def test_default_mode_finds_fewer_groups(self) -> None:
        """Without normalization flags, some groups should not appear."""
        self.assertLess(len(self.groups_default), len(self.groups_normalized))


class TestNearMatches(unittest.TestCase):
    """Regression tests for pydry near mode against the demo corpus."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.results = near_matches(DEMO_ROOT, threshold=0.75)
        cls.pairs = {_pair_key(r): r for r in cls.results}

    def test_finds_at_least_ten_pairs(self) -> None:
        self.assertGreaterEqual(len(self.results), 10)

    # ── Pattern label assertions ─────────────────────────────────

    def test_partial_application_label(self) -> None:
        """Currying formatters should be labeled partial_application."""
        pair = self.pairs.get(("make_prefix_formatter", "make_suffix_formatter"))
        self.assertIsNotNone(
            pair,
            "make_prefix_formatter <-> make_suffix_formatter not found",
        )
        self.assertIn("partial_application", pair.pattern_labels)

    def test_renamed_locals_label(self) -> None:
        """Pairs differing only in variable names should be labeled renamed_locals."""
        pair = self.pairs.get(("paginate_logs", "paginate_results"))
        self.assertIsNotNone(pair)
        self.assertIn("renamed_locals", pair.pattern_labels)

    def test_literal_specialization_label(self) -> None:
        """Query builders should be labeled literal_specialization."""
        pair = self.pairs.get(("build_admin_query", "build_user_query"))
        self.assertIsNotNone(pair)
        self.assertIn("literal_specialization", pair.pattern_labels)

    def test_extract_helper_candidate_label(self) -> None:
        """Record cleaners should be labeled extract_helper_candidate."""
        pair = self.pairs.get(("clean_record", "process_csv_row"))
        self.assertIsNotNone(pair)
        self.assertIn("extract_helper_candidate", pair.pattern_labels)

    def test_same_shape_different_dependencies_label(self) -> None:
        """write_yaml <-> write_toml should be labeled
        same_shape_different_dependencies."""
        pair = self.pairs.get(("write_toml", "write_yaml"))
        self.assertIsNotNone(pair, "write_yaml <-> write_toml not found")
        self.assertIn("same_shape_different_dependencies", pair.pattern_labels)

    # ── Risk flag assertions ─────────────────────────────────────

    def test_side_effects_risk_flag(self) -> None:
        """Functions calling open() should have possible_side_effects flag."""
        pair = self.pairs.get(("write_toml", "write_yaml"))
        self.assertIsNotNone(pair)
        self.assertIn("possible_side_effects", pair.risk_flags)

    def test_ambient_dependency_diff_risk_flag(self) -> None:
        """load_and_transform <-> read_and_convert should flag
        ambient_dependency_diff."""
        pair = self.pairs.get(("load_and_transform", "read_and_convert"))
        self.assertIsNotNone(pair)
        self.assertIn("ambient_dependency_diff", pair.risk_flags)

    # ── Refactor kind assertions ─────────────────────────────────

    def test_introduce_partial_suggestion(self) -> None:
        pair = self.pairs.get(("make_prefix_formatter", "make_suffix_formatter"))
        self.assertIsNotNone(pair)
        self.assertEqual(pair.suggested_refactor_kind, "introduce_partial")

    def test_parameterize_constant_suggestion(self) -> None:
        pair = self.pairs.get(("format_error_message", "format_warning_message"))
        self.assertIsNotNone(pair)
        self.assertEqual(pair.suggested_refactor_kind, "parameterize_constant")

    def test_extract_common_helper_suggestion(self) -> None:
        pair = self.pairs.get(("write_toml", "write_yaml"))
        self.assertIsNotNone(pair)
        self.assertEqual(pair.suggested_refactor_kind, "extract_common_helper")

    # ── Ordering / scoring assertions ────────────────────────────

    def test_results_sorted_by_refactorability_desc(self) -> None:
        scores = [r.refactorability_score for r in self.results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_highest_refactorability_is_curry_pair(self) -> None:
        top = self.results[0]
        names = {top.a.qualname, top.b.qualname}
        self.assertEqual(names, {"make_prefix_formatter", "make_suffix_formatter"})

    # ── Evidence value sanity checks ─────────────────────────────

    def test_class_method_pair_high_shape_similarity(self) -> None:
        pair = self.pairs.get(("OrderSerializer.to_dict", "UserSerializer.to_dict"))
        self.assertIsNotNone(pair)
        self.assertGreaterEqual(pair.evidence.shape_similarity, 0.85)

    def test_all_similarity_scores_in_range(self) -> None:
        for r in self.results:
            self.assertGreaterEqual(r.similarity_score, 0.0)
            self.assertLessEqual(r.similarity_score, 1.0)
            self.assertGreaterEqual(r.refactorability_score, 0.0)
            self.assertLessEqual(r.refactorability_score, 1.0)


class TestNearMatchBelowThreshold(unittest.TestCase):
    """Assert that structurally divergent pairs do NOT appear at 0.75."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.results = near_matches(DEMO_ROOT, threshold=0.75)
        cls.pairs = {_pair_key(r): r for r in cls.results}

    def test_async_sync_pair_excluded(self) -> None:
        """fetch_user <-> fetch_user_async should not match at 0.75."""
        pair = self.pairs.get(("fetch_user", "fetch_user_async"))
        self.assertIsNone(pair, "async/sync pair should not match at threshold 0.75")

    def test_generator_vs_list_excluded(self) -> None:
        """collect_even <-> iter_even should not match at 0.75."""
        pair = self.pairs.get(("collect_even", "iter_even"))
        self.assertIsNone(
            pair,
            "generator vs list pair should not match at threshold 0.75",
        )

    def test_strict_vs_lenient_excluded(self) -> None:
        """parse_int_strict <-> parse_int_lenient should not match at 0.75."""
        pair = self.pairs.get(("parse_int_lenient", "parse_int_strict"))
        self.assertIsNone(
            pair,
            "strict/lenient pair should not match at threshold 0.75",
        )


class TestAbstractCandidates(unittest.TestCase):
    """Regression tests for pydry abstract mode against the demo corpus."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.results = abstract_candidates(DEMO_ROOT, threshold=0.75)
        cls.pairs = {_pair_key(r): r for r in cls.results}

    def test_no_leave_separate_in_results(self) -> None:
        for r in self.results:
            self.assertNotEqual(
                r.suggested_refactor_kind,
                "leave_separate",
                f"{r.a.qualname} <-> {r.b.qualname} should not be leave_separate",
            )

    def test_abstract_is_subset_of_near(self) -> None:
        """Every abstract candidate should also appear in near matches."""
        near = near_matches(DEMO_ROOT, threshold=0.75)
        near_pairs = {_pair_key(r) for r in near}
        for r in self.results:
            pk = _pair_key(r)
            self.assertIn(pk, near_pairs, f"{pk} in abstract but not in near")

    def test_abstract_template_present_for_extract_helper(self) -> None:
        """Pairs labeled extract_helper_candidate should have an abstract_template."""
        for r in self.results:
            if "extract_helper_candidate" in r.pattern_labels:
                self.assertIsNotNone(
                    r.abstract_template,
                    f"{r.a.qualname} <-> {r.b.qualname} missing abstract_template",
                )


if __name__ == "__main__":
    unittest.main()
