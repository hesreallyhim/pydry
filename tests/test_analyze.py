"""Tests for pydry.analyze — edge cases in function iteration,
LCS ratio, feature extraction, and occurrence construction.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from pydry.analyze import (
    _lcs_ratio,
    extract_features,
    iter_functions,
    occurrence_for,
)


def _parse_module(src: str) -> ast.Module:
    return ast.parse(src)


def _parse_func(src: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    module = ast.parse(src)
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise ValueError("No function found in source")


class TestIterFunctionsTopLevelOnly(unittest.TestCase):
    """iter_functions with top_level_only=True."""

    def test_top_level_only_excludes_nested(self) -> None:
        src = """\
def outer():
    def inner():
        return 1
    return inner()

class Foo:
    def method(self):
        pass
"""
        module = _parse_module(src)
        results = list(iter_functions(module, top_level_only=True))
        # Should only yield top-level functions, not nested or class methods
        self.assertEqual(len(results), 1)
        fn, parents, is_cm = results[0]
        self.assertEqual(fn.name, "outer")
        self.assertEqual(parents, [])
        self.assertFalse(is_cm)

    def test_top_level_only_skips_classes(self) -> None:
        src = """\
class MyClass:
    def method(self):
        pass

def standalone():
    return 42
"""
        module = _parse_module(src)
        results = list(iter_functions(module, top_level_only=True))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0].name, "standalone")

    def test_top_level_only_includes_async_functions(self) -> None:
        src = """\
async def fetch():
    return await get_data()

def sync():
    return 1
"""
        module = _parse_module(src)
        results = list(iter_functions(module, top_level_only=True))
        names = {r[0].name for r in results}
        self.assertEqual(names, {"fetch", "sync"})

    def test_top_level_only_empty_module(self) -> None:
        src = "x = 1\ny = 2\n"
        module = _parse_module(src)
        results = list(iter_functions(module, top_level_only=True))
        self.assertEqual(results, [])


class TestLcsRatio(unittest.TestCase):
    """_lcs_ratio edge cases."""

    def test_both_empty(self) -> None:
        self.assertEqual(_lcs_ratio([], []), 1.0)

    def test_one_empty(self) -> None:
        self.assertEqual(_lcs_ratio(["a"], []), 0.0)
        self.assertEqual(_lcs_ratio([], ["b"]), 0.0)

    def test_identical_lists(self) -> None:
        self.assertEqual(_lcs_ratio(["a", "b", "c"], ["a", "b", "c"]), 1.0)

    def test_completely_different(self) -> None:
        self.assertEqual(_lcs_ratio(["a", "b"], ["c", "d"]), 0.0)

    def test_partial_overlap(self) -> None:
        ratio = _lcs_ratio(["a", "b", "c"], ["a", "x", "c"])
        # LCS is ["a", "c"] = 2, total = 3+3 = 6, ratio = 4/6 ~= 0.667
        self.assertAlmostEqual(ratio, 4.0 / 6.0, places=5)

    def test_shorter_first_arg(self) -> None:
        """Verify the swap branch when shorter > longer is triggered."""
        ratio = _lcs_ratio(["a", "b", "c", "d"], ["a", "c"])
        self.assertGreater(ratio, 0.0)
        self.assertLessEqual(ratio, 1.0)


class TestExtractFeatures(unittest.TestCase):
    """Feature extraction edge cases: curry_depth, returns_lambda."""

    def test_curry_depth_and_returns_lambda(self) -> None:
        src = """\
def make_adder(x):
    return lambda y: lambda z: x + y + z
"""
        fn = _parse_func(src)
        features = extract_features(fn)
        self.assertTrue(features["returns_lambda"])
        self.assertEqual(features["curry_depth"], 2)

    def test_no_lambda_features(self) -> None:
        src = "def f(x):\n    return x + 1\n"
        fn = _parse_func(src)
        features = extract_features(fn)
        self.assertFalse(features["returns_lambda"])
        self.assertEqual(features["curry_depth"], 0)

    def test_single_lambda_return(self) -> None:
        src = "def f(x):\n    return lambda y: x + y\n"
        fn = _parse_func(src)
        features = extract_features(fn)
        self.assertTrue(features["returns_lambda"])
        self.assertEqual(features["curry_depth"], 1)

    def test_wrapper_detection(self) -> None:
        src = "def wrapper(x):\n    return inner(x)\n"
        fn = _parse_func(src)
        features = extract_features(fn)
        self.assertTrue(features["is_wrapper"])
        self.assertEqual(features["wrapper_target"], "inner")
        self.assertEqual(features["passthrough_args"], 1)
        self.assertEqual(features["fixed_args"], 0)

    def test_wrapper_with_fixed_args(self) -> None:
        src = "def wrapper(x):\n    return inner(x, 42, 'hello')\n"
        fn = _parse_func(src)
        features = extract_features(fn)
        self.assertTrue(features["is_wrapper"])
        self.assertEqual(features["passthrough_args"], 1)
        self.assertEqual(features["fixed_args"], 2)

    def test_multi_statement_not_wrapper(self) -> None:
        src = "def f(x):\n    y = x + 1\n    return y\n"
        fn = _parse_func(src)
        features = extract_features(fn)
        self.assertFalse(features["is_wrapper"])

    def test_expression_only_wrapper(self) -> None:
        """A single Expr-statement wrapping a call is detected as wrapper."""
        src = "def f(x):\n    inner(x)\n"
        fn = _parse_func(src)
        features = extract_features(fn)
        self.assertTrue(features["is_wrapper"])
        self.assertEqual(features["wrapper_target"], "inner")

    def test_has_yield(self) -> None:
        src = "def gen():\n    yield 1\n    yield 2\n"
        fn = _parse_func(src)
        features = extract_features(fn)
        self.assertTrue(features["has_yield"])

    def test_has_await(self) -> None:
        src = "async def f():\n    await do_stuff()\n"
        fn = _parse_func(src)
        features = extract_features(fn)
        self.assertTrue(features["has_await"])


class TestOccurrenceFor(unittest.TestCase):
    """occurrence_for edge cases: missing end_lineno, is_method_flag override."""

    def test_missing_end_lineno(self) -> None:
        src = "def f():\n    return 1\n"
        fn = _parse_func(src)
        # Simulate a node without end_lineno
        if hasattr(fn, "end_lineno"):
            del fn.end_lineno
        occ = occurrence_for(Path("test.py"), fn, [])
        self.assertIsNone(occ.end_lineno)

    def test_missing_lineno(self) -> None:
        src = "def f():\n    return 1\n"
        fn = _parse_func(src)
        # Simulate a node without lineno
        if hasattr(fn, "lineno"):
            del fn.lineno
        occ = occurrence_for(Path("test.py"), fn, [])
        self.assertEqual(occ.lineno, 0)

    def test_missing_col_offset(self) -> None:
        src = "def f():\n    return 1\n"
        fn = _parse_func(src)
        if hasattr(fn, "col_offset"):
            del fn.col_offset
        occ = occurrence_for(Path("test.py"), fn, [])
        self.assertEqual(occ.col_offset, 0)

    def test_is_method_flag_override(self) -> None:
        src = "def f():\n    return 1\n"
        fn = _parse_func(src)
        # Even with empty parents, override should force is_method=True
        occ_true = occurrence_for(Path("test.py"), fn, [], is_method_flag=True)
        self.assertTrue(occ_true.is_method)

        occ_false = occurrence_for(
            Path("test.py"), fn, ["SomeClass"], is_method_flag=False
        )
        self.assertFalse(occ_false.is_method)

    def test_is_method_default_infers_from_parents(self) -> None:
        src = "def f():\n    return 1\n"
        fn = _parse_func(src)
        occ_no_parents = occurrence_for(Path("test.py"), fn, [])
        self.assertFalse(occ_no_parents.is_method)

        occ_with_parents = occurrence_for(Path("test.py"), fn, ["MyClass"])
        self.assertTrue(occ_with_parents.is_method)

    def test_async_function_kind(self) -> None:
        src = "async def f():\n    return 1\n"
        fn = _parse_func(src)
        occ = occurrence_for(Path("test.py"), fn, [])
        self.assertEqual(occ.kind, "async def")

    def test_qualname_with_parents(self) -> None:
        src = "def f():\n    return 1\n"
        fn = _parse_func(src)
        occ = occurrence_for(Path("test.py"), fn, ["Outer", "Inner"])
        self.assertEqual(occ.qualname, "Outer.Inner.f")


if __name__ == "__main__":
    unittest.main()
