"""Tests for pydry.engine — scoring, labeling, and refactor suggestion edge cases.

Covers _wrapper_score, _curry_score, _risk_flags, _pattern_labels,
_suggest_refactor, _difference_notes, _shared_summary, _abstract_template,
_refactorability, and exact_groups with include_canonical.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from collections import Counter
from pathlib import Path
from typing import Any

from pydry.engine import (
    _abstract_template,
    _curry_score,
    _difference_notes,
    _pattern_labels,
    _refactorability,
    _risk_flags,
    _shared_summary,
    _suggest_refactor,
    _wrapper_score,
    exact_groups,
    near_matches,
)
from pydry.models import FunctionOccurrence, SimilarityEvidence


def _base_features(**overrides: Any) -> dict[str, Any]:
    """Create a baseline feature dict with sensible defaults."""
    defaults: dict[str, Any] = {
        "param_count": 1,
        "has_yield": False,
        "has_await": False,
        "control_count": 1,
        "returns": 1,
        "raises": 0,
        "literals": 2,
        "literal_tokens": Counter(),
        "side_effect_calls": [],
        "is_wrapper": False,
        "wrapper_target": None,
        "fixed_args": 0,
        "passthrough_args": 0,
        "returns_lambda": False,
        "curry_depth": 0,
        "stmt_count": 3,
        "node_types": Counter({"FunctionDef": 1, "Return": 1, "Name": 2}),
        "stmt_seq": ["Return"],
        "call_names": Counter(),
        "external_names": Counter(),
    }
    defaults.update(overrides)
    return defaults


def _make_evidence(**overrides: Any) -> SimilarityEvidence:
    defaults = {
        "shape_similarity": 0.5,
        "stmt_similarity": 0.5,
        "call_similarity": 0.5,
        "signature_similarity": 0.5,
        "wrapper_score": 0.0,
        "curry_score": 0.0,
    }
    defaults.update(overrides)
    return SimilarityEvidence(**defaults)


def _make_occ(qualname: str = "f") -> FunctionOccurrence:
    return FunctionOccurrence(
        path="test.py",
        lineno=1,
        end_lineno=10,
        col_offset=0,
        name=qualname.split(".")[-1],
        qualname=qualname,
        kind="def",
        param_count=1,
        is_method=False,
    )


class TestWrapperScore(unittest.TestCase):
    def test_both_wrappers_same_target(self) -> None:
        a = _base_features(is_wrapper=True, wrapper_target="inner")
        b = _base_features(is_wrapper=True, wrapper_target="inner")
        self.assertAlmostEqual(_wrapper_score(a, b), 0.85, places=5)

    def test_both_wrappers_different_targets(self) -> None:
        a = _base_features(is_wrapper=True, wrapper_target="inner")
        b = _base_features(is_wrapper=True, wrapper_target="other")
        self.assertAlmostEqual(_wrapper_score(a, b), 0.5, places=5)

    def test_both_wrappers_none_target(self) -> None:
        a = _base_features(is_wrapper=True, wrapper_target=None)
        b = _base_features(is_wrapper=True, wrapper_target=None)
        # Same target but None, so the None check prevents +0.35
        self.assertAlmostEqual(_wrapper_score(a, b), 0.5, places=5)

    def test_only_one_wrapper(self) -> None:
        a = _base_features(is_wrapper=True, wrapper_target="inner")
        b = _base_features(is_wrapper=False)
        self.assertAlmostEqual(_wrapper_score(a, b), 0.25, places=5)

    def test_neither_wrapper(self) -> None:
        a = _base_features()
        b = _base_features()
        self.assertAlmostEqual(_wrapper_score(a, b), 0.0, places=5)


class TestCurryScore(unittest.TestCase):
    def test_both_return_lambda_same_depth(self) -> None:
        a = _base_features(returns_lambda=True, curry_depth=2)
        b = _base_features(returns_lambda=True, curry_depth=2)
        self.assertAlmostEqual(_curry_score(a, b), 0.8, places=5)

    def test_both_return_lambda_different_depth(self) -> None:
        a = _base_features(returns_lambda=True, curry_depth=1)
        b = _base_features(returns_lambda=True, curry_depth=2)
        self.assertAlmostEqual(_curry_score(a, b), 0.6, places=5)

    def test_only_one_returns_lambda(self) -> None:
        a = _base_features(returns_lambda=True, curry_depth=1)
        b = _base_features(returns_lambda=False, curry_depth=0)
        self.assertAlmostEqual(_curry_score(a, b), 0.4, places=5)

    def test_neither_returns_lambda(self) -> None:
        a = _base_features()
        b = _base_features()
        self.assertAlmostEqual(_curry_score(a, b), 0.0, places=5)


class TestRiskFlags(unittest.TestCase):
    def test_no_risks(self) -> None:
        a = _base_features()
        b = _base_features()
        self.assertEqual(_risk_flags(a, b), [])

    def test_side_effects(self) -> None:
        a = _base_features(side_effect_calls=["open"])
        b = _base_features()
        flags = _risk_flags(a, b)
        self.assertIn("possible_side_effects", flags)

    def test_async_boundary_diff(self) -> None:
        a = _base_features(has_await=True)
        b = _base_features(has_await=False)
        flags = _risk_flags(a, b)
        self.assertIn("async_boundary_diff", flags)

    def test_return_shape_diff(self) -> None:
        a = _base_features(has_yield=True)
        b = _base_features(has_yield=False)
        flags = _risk_flags(a, b)
        self.assertIn("return_shape_diff", flags)

    def test_exception_behavior_diff(self) -> None:
        a = _base_features(raises=2)
        b = _base_features(raises=0)
        flags = _risk_flags(a, b)
        self.assertIn("exception_behavior_diff", flags)

    def test_ambient_dependency_diff(self) -> None:
        a = _base_features(
            external_names=Counter({"a": 1, "b": 1, "c": 1, "d": 1, "e": 1, "f": 1})
        )
        b = _base_features(external_names=Counter())
        flags = _risk_flags(a, b)
        self.assertIn("ambient_dependency_diff", flags)


class TestDifferenceNotes(unittest.TestCase):
    def test_no_differences(self) -> None:
        a = _base_features()
        b = _base_features()
        self.assertEqual(_difference_notes(a, b), [])

    def test_param_count_differs(self) -> None:
        a = _base_features(param_count=1)
        b = _base_features(param_count=3)
        notes = _difference_notes(a, b)
        self.assertIn("parameter count differs (1 vs 3)", notes)

    def test_async_behavior_differs(self) -> None:
        a = _base_features(has_await=True)
        b = _base_features(has_await=False)
        notes = _difference_notes(a, b)
        self.assertIn("async behavior differs", notes)

    def test_generator_behavior_differs(self) -> None:
        a = _base_features(has_yield=True)
        b = _base_features(has_yield=False)
        notes = _difference_notes(a, b)
        self.assertIn("generator behavior differs", notes)

    def test_exception_behavior_differs(self) -> None:
        a = _base_features(raises=1)
        b = _base_features(raises=0)
        notes = _difference_notes(a, b)
        self.assertIn("exception behavior differs", notes)

    def test_wrapper_targets_differ(self) -> None:
        a = _base_features(is_wrapper=True, wrapper_target="foo")
        b = _base_features(is_wrapper=True, wrapper_target="bar")
        notes = _difference_notes(a, b)
        self.assertIn("wrapper targets differ", notes)

    def test_literal_density_differs(self) -> None:
        a = _base_features(literals=5)
        b = _base_features(literals=1)
        notes = _difference_notes(a, b)
        self.assertIn("literal density differs", notes)

    def test_control_flow_differs(self) -> None:
        a = _base_features(control_count=5)
        b = _base_features(control_count=1)
        notes = _difference_notes(a, b)
        self.assertIn("control-flow complexity differs", notes)


class TestPatternLabels(unittest.TestCase):
    def test_wrapper_label(self) -> None:
        a = _base_features()
        b = _base_features()
        evidence = _make_evidence(wrapper_score=0.5)
        labels = _pattern_labels(a, b, evidence)
        self.assertIn("wrapper", labels)

    def test_partial_application_label(self) -> None:
        a = _base_features()
        b = _base_features()
        evidence = _make_evidence(curry_score=0.4)
        labels = _pattern_labels(a, b, evidence)
        self.assertIn("partial_application", labels)

    def test_renamed_locals_label(self) -> None:
        a = _base_features()
        b = _base_features()
        evidence = _make_evidence(shape_similarity=0.95, call_similarity=0.9)
        labels = _pattern_labels(a, b, evidence)
        self.assertIn("renamed_locals", labels)

    def test_same_shape_different_dependencies_label(self) -> None:
        a = _base_features(param_count=2)
        b = _base_features(param_count=2)
        evidence = _make_evidence(signature_similarity=0.9, call_similarity=0.3)
        labels = _pattern_labels(a, b, evidence)
        self.assertIn("same_shape_different_dependencies", labels)


class TestSharedSummary(unittest.TestCase):
    def test_no_overlap(self) -> None:
        a = _base_features(
            stmt_seq=["Return"],
            call_names=Counter({"foo": 1}),
        )
        b = _base_features(
            stmt_seq=["Assign"],
            call_names=Counter({"bar": 1}),
        )
        summary = _shared_summary(a, b)
        self.assertIn("shared AST shape without strong call overlap", summary)

    def test_shared_calls(self) -> None:
        a = _base_features(
            stmt_seq=["Return"],
            call_names=Counter({"foo": 1, "bar": 1}),
        )
        b = _base_features(
            stmt_seq=["Assign"],
            call_names=Counter({"foo": 1, "baz": 1}),
        )
        summary = _shared_summary(a, b)
        self.assertIn("shared calls: foo", summary)


class TestSuggestRefactor(unittest.TestCase):
    def test_wrapper_suggestion(self) -> None:
        evidence = _make_evidence(wrapper_score=0.5)
        result = _suggest_refactor(["wrapper"], [], evidence)
        self.assertEqual(result, "merge_into_single_function_with_param")

    def test_partial_application_suggestion(self) -> None:
        evidence = _make_evidence()
        result = _suggest_refactor(["partial_application"], [], evidence)
        self.assertEqual(result, "introduce_partial")

    def test_extract_common_helper_suggestion(self) -> None:
        evidence = _make_evidence()
        result = _suggest_refactor(["extract_helper_candidate"], [], evidence)
        self.assertEqual(result, "extract_common_helper")

    def test_extract_common_helper_blocked_by_side_effects(self) -> None:
        evidence = _make_evidence()
        result = _suggest_refactor(
            ["extract_helper_candidate"], ["possible_side_effects"], evidence
        )
        # Falls through since side effects block extract_common_helper
        self.assertNotEqual(result, "extract_common_helper")

    def test_parameterize_constant_suggestion(self) -> None:
        evidence = _make_evidence()
        result = _suggest_refactor(["literal_specialization"], [], evidence)
        self.assertEqual(result, "parameterize_constant")

    def test_leave_separate_for_async_boundary(self) -> None:
        evidence = _make_evidence()
        result = _suggest_refactor([], ["async_boundary_diff"], evidence)
        self.assertEqual(result, "leave_separate")

    def test_leave_separate_for_return_shape_diff(self) -> None:
        evidence = _make_evidence()
        result = _suggest_refactor([], ["return_shape_diff"], evidence)
        self.assertEqual(result, "leave_separate")

    def test_leave_separate_for_ambient_dependency(self) -> None:
        evidence = _make_evidence()
        result = _suggest_refactor([], ["ambient_dependency_diff"], evidence)
        self.assertEqual(result, "leave_separate")

    def test_move_to_utils_fallback(self) -> None:
        evidence = _make_evidence()
        result = _suggest_refactor([], [], evidence)
        self.assertEqual(result, "move_to_utils")


class TestAbstractTemplate(unittest.TestCase):
    def test_extract_helper_template(self) -> None:
        result = _abstract_template(
            _make_occ("alpha"),
            _make_occ("beta"),
            ["extract_helper_candidate"],
            "shared calls: foo",
        )
        assert result is not None
        self.assertIn("alpha", result)
        self.assertIn("beta", result)

    def test_wrapper_template(self) -> None:
        result = _abstract_template(
            _make_occ("a"),
            _make_occ("b"),
            ["wrapper"],
            "shared calls: inner",
        )
        self.assertIsNotNone(result)

    def test_no_template_for_unrelated_labels(self) -> None:
        result = _abstract_template(
            _make_occ("a"),
            _make_occ("b"),
            ["renamed_locals"],
            "some summary",
        )
        self.assertIsNone(result)


class TestRefactorability(unittest.TestCase):
    def test_high_score_with_good_evidence(self) -> None:
        evidence = _make_evidence(
            shape_similarity=0.95,
            stmt_similarity=0.9,
            call_similarity=0.85,
            signature_similarity=0.9,
            wrapper_score=0.8,
            curry_score=0.6,
        )
        score = _refactorability(
            ["extract_helper_candidate", "literal_specialization"], [], evidence
        )
        self.assertGreater(score, 0.8)
        self.assertLessEqual(score, 1.0)

    def test_risks_reduce_score(self) -> None:
        evidence = _make_evidence(shape_similarity=0.9, stmt_similarity=0.8)
        score_no_risks = _refactorability([], [], evidence)
        score_with_risks = _refactorability(
            [], ["possible_side_effects", "async_boundary_diff"], evidence
        )
        self.assertGreater(score_no_risks, score_with_risks)

    def test_clamped_to_zero(self) -> None:
        evidence = _make_evidence(
            shape_similarity=0.0,
            stmt_similarity=0.0,
            call_similarity=0.0,
            signature_similarity=0.0,
        )
        score = _refactorability(
            [],
            ["a", "b", "c", "d", "e"],
            evidence,
        )
        self.assertEqual(score, 0.0)


class TestExactGroupsCanonical(unittest.TestCase):
    """exact_groups with include_canonical=True."""

    def _make_repo(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content))
        return root

    def test_include_canonical_populates_field(self) -> None:
        root = self._make_repo(
            {
                "a.py": "def f(x):\n    return x + 1\n",
                "b.py": "def g(y):\n    return y + 1\n",
            }
        )
        groups = exact_groups(
            root,
            min_count=2,
            include_canonical=True,
            normalize_local_names=True,
        )
        self.assertTrue(groups)
        self.assertIsNotNone(groups[0].canonical)

    def test_include_canonical_false_is_none(self) -> None:
        root = self._make_repo(
            {
                "a.py": "def f(x):\n    return x + 1\n",
                "b.py": "def g(y):\n    return y + 1\n",
            }
        )
        groups = exact_groups(
            root,
            min_count=2,
            include_canonical=False,
            normalize_local_names=True,
        )
        self.assertTrue(groups)
        self.assertIsNone(groups[0].canonical)


class TestNearMatchesTopKOverflow(unittest.TestCase):
    """Test the top_k eviction branch in near_matches."""

    def _make_repo(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content))
        return root

    def test_top_k_evicts_worst_entries(self) -> None:
        """With many similar functions and small top_k, results are capped."""
        root = self._make_repo(
            {
                "a.py": """
                def f1(x):
                    return x + 1

                def f2(y):
                    return y + 1

                def f3(z):
                    return z + 1

                def f4(w):
                    return w + 1
            """,
                "b.py": """
                def g1(x):
                    return x + 1

                def g2(y):
                    return y + 1
            """,
            }
        )
        # Many pairs possible, but top_k=2 should limit results
        results = near_matches(root, threshold=0.5, top_k=2)
        self.assertEqual(len(results), 2)

    def test_scan_errors_none_branch(self) -> None:
        """When scan_errors is None (not passed), errors are silently dropped."""
        root = self._make_repo(
            {
                "good.py": "def f(x):\n    return x + 1\n",
                "bad.py": "def broken(:\n    pass\n",
            }
        )
        # Should not raise, errors silently ignored
        results = near_matches(root, threshold=0.99)
        self.assertIsInstance(results, list)


if __name__ == "__main__":
    unittest.main()
