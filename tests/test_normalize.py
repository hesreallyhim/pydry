"""Tests for pydry.normalize — edge cases in AST normalization.

Covers keyword/builtin preservation, argument name normalization,
exception handler name normalization, global/nonlocal visitors,
and constant normalization for bytes/complex/float/fallback types.
Also covers FunctionNormalizer annotation stripping and arg handling.
"""

from __future__ import annotations

import ast
import unittest

from pydry.normalize import ConstantNormalizer, FunctionNormalizer, LocalNameNormalizer


def _parse_func(src: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Parse a single function definition from source."""
    module = ast.parse(src)
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise ValueError("No function found in source")


class TestLocalNameNormalizerPreserve(unittest.TestCase):
    """_preserve() edge cases — keywords and builtins."""

    def test_keyword_is_preserved(self) -> None:
        normalizer = LocalNameNormalizer()
        self.assertTrue(normalizer._preserve("for"))
        self.assertTrue(normalizer._preserve("if"))
        self.assertTrue(normalizer._preserve("return"))

    def test_builtins_true_false_none_preserved(self) -> None:
        normalizer = LocalNameNormalizer()
        self.assertTrue(normalizer._preserve("True"))
        self.assertTrue(normalizer._preserve("False"))
        self.assertTrue(normalizer._preserve("None"))

    def test_self_cls_preserved_by_default(self) -> None:
        normalizer = LocalNameNormalizer()
        self.assertTrue(normalizer._preserve("self"))
        self.assertTrue(normalizer._preserve("cls"))

    def test_self_cls_not_preserved_when_disabled(self) -> None:
        normalizer = LocalNameNormalizer(preserve_self_cls=False)
        self.assertFalse(normalizer._preserve("self"))
        self.assertFalse(normalizer._preserve("cls"))

    def test_regular_name_not_preserved(self) -> None:
        normalizer = LocalNameNormalizer()
        self.assertFalse(normalizer._preserve("foo"))
        self.assertFalse(normalizer._preserve("my_var"))


class TestLocalNameNormalizerVisitors(unittest.TestCase):
    """visit_arg, visit_ExceptHandler, visit_Global, visit_Nonlocal."""

    def test_visit_arg_normalizes_regular_arg(self) -> None:
        normalizer = LocalNameNormalizer()
        arg_node = ast.arg(arg="my_param", annotation=None)
        result = normalizer.visit_arg(arg_node)
        self.assertEqual(result.arg, "v0")

    def test_visit_arg_preserves_self(self) -> None:
        normalizer = LocalNameNormalizer()
        arg_node = ast.arg(arg="self", annotation=None)
        result = normalizer.visit_arg(arg_node)
        self.assertEqual(result.arg, "self")

    def test_visit_except_handler_normalizes_name(self) -> None:
        src = """\
def f():
    try:
        pass
    except Exception as err:
        print(err)
"""
        tree = ast.parse(src)
        normalizer = LocalNameNormalizer()
        result = normalizer.visit(tree)
        handlers = [n for n in ast.walk(result) if isinstance(n, ast.ExceptHandler)]
        self.assertEqual(len(handlers), 1)
        # "err" should be normalized to a token like "v0" or similar
        self.assertIsNotNone(handlers[0].name)
        name = handlers[0].name
        assert name is not None
        self.assertTrue(name.startswith("v"))

    def test_visit_except_handler_preserves_none_name(self) -> None:
        """ExceptHandler with no 'as' name should remain None."""
        src = """\
def f():
    try:
        pass
    except Exception:
        pass
"""
        tree = ast.parse(src)
        normalizer = LocalNameNormalizer()
        result = normalizer.visit(tree)
        handlers = [n for n in ast.walk(result) if isinstance(n, ast.ExceptHandler)]
        self.assertEqual(len(handlers), 1)
        self.assertIsNone(handlers[0].name)

    def test_visit_global_returns_node_unchanged(self) -> None:
        normalizer = LocalNameNormalizer()
        node = ast.Global(names=["x", "y"])
        result = normalizer.visit_Global(node)
        self.assertIs(result, node)
        self.assertEqual(result.names, ["x", "y"])

    def test_visit_nonlocal_returns_node_unchanged(self) -> None:
        normalizer = LocalNameNormalizer()
        node = ast.Nonlocal(names=["a", "b"])
        result = normalizer.visit_Nonlocal(node)
        self.assertIs(result, node)
        self.assertEqual(result.names, ["a", "b"])


class TestConstantNormalizer(unittest.TestCase):
    """visit_Constant for bytes, complex, float, and fallback."""

    def test_string_normalized(self) -> None:
        normalizer = ConstantNormalizer()
        node = ast.Constant(value="hello world")
        result = normalizer.visit_Constant(node)
        self.assertEqual(result.value, "__str__")

    def test_bytes_normalized(self) -> None:
        normalizer = ConstantNormalizer()
        node = ast.Constant(value=b"binary data")
        result = normalizer.visit_Constant(node)
        self.assertEqual(result.value, b"__bytes__")

    def test_bool_preserved(self) -> None:
        normalizer = ConstantNormalizer()
        node_true = ast.Constant(value=True)
        node_false = ast.Constant(value=False)
        self.assertIs(normalizer.visit_Constant(node_true).value, True)
        self.assertIs(normalizer.visit_Constant(node_false).value, False)

    def test_none_preserved(self) -> None:
        normalizer = ConstantNormalizer()
        node = ast.Constant(value=None)
        self.assertIsNone(normalizer.visit_Constant(node).value)

    def test_int_normalized(self) -> None:
        normalizer = ConstantNormalizer()
        node = ast.Constant(value=42)
        result = normalizer.visit_Constant(node)
        self.assertEqual(result.value, 0)

    def test_float_normalized(self) -> None:
        normalizer = ConstantNormalizer()
        node = ast.Constant(value=3.14)
        result = normalizer.visit_Constant(node)
        self.assertEqual(result.value, 0.0)

    def test_complex_normalized(self) -> None:
        normalizer = ConstantNormalizer()
        node = ast.Constant(value=2 + 3j)
        result = normalizer.visit_Constant(node)
        self.assertEqual(result.value, 0j)

    def test_unknown_type_fallback(self) -> None:
        """A Constant with a non-standard value type falls through to __const__."""
        normalizer = ConstantNormalizer()
        # Manually set value to something unusual (like a tuple, which
        # shouldn't appear in real AST constants but exercises the fallback)
        node = ast.Constant(value=(1, 2))  # type: ignore[arg-type]
        result = normalizer.visit_Constant(node)
        self.assertEqual(result.value, "__const__")


class TestFunctionNormalizerAnnotations(unittest.TestCase):
    """_normalize() annotation/arg handling paths."""

    def test_strip_annotations_removes_return_type(self) -> None:
        src = "def f(x: int) -> str:\n    return str(x)\n"
        fn = _parse_func(src)
        normalizer = FunctionNormalizer(strip_annotations=True)
        result = normalizer.visit(fn)
        self.assertIsNone(result.returns)

    def test_strip_annotations_removes_arg_annotations(self) -> None:
        src = "def f(x: int, y: str) -> None:\n    pass\n"
        fn = _parse_func(src)
        normalizer = FunctionNormalizer(strip_annotations=True)
        result = normalizer.visit(fn)
        for arg in result.args.args:
            self.assertIsNone(arg.annotation)

    def test_strip_annotations_removes_vararg_kwarg_annotations(self) -> None:
        src = "def f(*args: int, **kwargs: str) -> None:\n    pass\n"
        fn = _parse_func(src)
        normalizer = FunctionNormalizer(strip_annotations=True)
        result = normalizer.visit(fn)
        self.assertIsNotNone(result.args.vararg)
        self.assertIsNone(result.args.vararg.annotation)
        self.assertIsNotNone(result.args.kwarg)
        self.assertIsNone(result.args.kwarg.annotation)

    def test_normalize_arg_names_for_vararg_kwarg(self) -> None:
        src = "def f(*args, **kwargs):\n    pass\n"
        fn = _parse_func(src)
        normalizer = FunctionNormalizer(normalize_arg_names=True)
        result = normalizer.visit(fn)
        self.assertEqual(result.args.vararg.arg, "vararg")
        self.assertEqual(result.args.kwarg.arg, "kwarg")

    def test_preserve_function_name(self) -> None:
        src = "def my_special_function():\n    return 1\n"
        fn = _parse_func(src)
        normalizer = FunctionNormalizer(preserve_function_name=True)
        result = normalizer.visit(fn)
        self.assertEqual(result.name, "my_special_function")

    def test_does_not_preserve_function_name_by_default(self) -> None:
        src = "def my_special_function():\n    return 1\n"
        fn = _parse_func(src)
        normalizer = FunctionNormalizer(preserve_function_name=False)
        result = normalizer.visit(fn)
        self.assertEqual(result.name, "__func__")

    def test_strip_docstring(self) -> None:
        src = 'def f():\n    """This is a docstring."""\n    return 1\n'
        fn = _parse_func(src)
        normalizer = FunctionNormalizer(strip_docstrings=True)
        result = normalizer.visit(fn)
        # The body should not start with the docstring
        first = result.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            self.assertNotIsInstance(first.value.value, str)

    def test_strip_decorators(self) -> None:
        src = "@staticmethod\ndef f():\n    return 1\n"
        fn = _parse_func(src)
        normalizer = FunctionNormalizer(strip_decorators=True)
        result = normalizer.visit(fn)
        self.assertEqual(result.decorator_list, [])

    def test_normalize_local_names_applied(self) -> None:
        src = "def f(x):\n    y = x + 1\n    return y\n"
        fn = _parse_func(src)
        normalizer = FunctionNormalizer(normalize_local_names=True)
        result = normalizer.visit(fn)
        # Verify that local names are normalized (Name nodes have token IDs)
        names = [n.id for n in ast.walk(result) if isinstance(n, ast.Name)]
        # All should be token-style names like v0, v1...
        self.assertTrue(any(name.startswith("v") for name in names))

    def test_normalize_constants_applied(self) -> None:
        src = "def f():\n    return 42\n"
        fn = _parse_func(src)
        normalizer = FunctionNormalizer(normalize_constants=True)
        result = normalizer.visit(fn)
        constants = [
            n.value
            for n in ast.walk(result)
            if isinstance(n, ast.Constant) and isinstance(n.value, int)
        ]
        # All ints should be 0
        for c in constants:
            self.assertEqual(c, 0)


if __name__ == "__main__":
    unittest.main()
