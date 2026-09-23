"""Tests for pydry.normalize — edge cases in AST normalization.

Covers keyword/builtin preservation, argument name normalization,
exception handler name normalization, global/nonlocal visitors,
and constant normalization for bytes/complex/float/fallback types.
Also covers FunctionNormalizer annotation stripping and arg handling.
"""

from __future__ import annotations

import ast
import unittest

from pydry.normalize import (
    ConstantNormalizer,
    FunctionNormalizer,
    LocalNameNormalizer,
    all_bindings,
    bound_names,
    is_placeholder,
)


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
        self.assertEqual(result.arg, "«v0»")

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
        self.assertTrue(is_placeholder(name))

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
        self.assertEqual(result.args.vararg.arg, "«arg0»")
        self.assertEqual(result.args.kwarg.arg, "«arg1»")

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
        self.assertTrue(any(is_placeholder(name) for name in names))

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


class ScopeAwareNormalizationTests(unittest.TestCase):
    """Bindings respect lexical scope and generated names cannot collide."""

    def _canonical(self, src: str, **opts: bool) -> str:
        from pydry.analyze import canonicalize

        return canonicalize(_parse_func(src), **opts)

    def test_closure_reference_is_distinct_from_nested_parameter(self) -> None:
        closure = (
            "def first(x):\n"
            "    def inner(y):\n"
            "        return x + y\n"
            "    value = inner(1)\n"
            "    return value\n"
        )
        local = closure.replace("return x + y", "return y + y")
        for normalize_locals in (False, True):
            with self.subTest(normalize_locals=normalize_locals):
                self.assertNotEqual(
                    self._canonical(closure, normalize_local_names=normalize_locals),
                    self._canonical(local, normalize_local_names=normalize_locals),
                )

    def test_renamed_closures_keep_equivalent_bindings(self) -> None:
        first = (
            "def first(x):\n"
            "    def inner(y):\n"
            "        return x + y\n"
            "    value = inner(1)\n"
            "    return value\n"
        )
        second = (
            first.replace("first(x)", "second(outer)")
            .replace("inner(y)", "inner(arg0)")
            .replace("return x + y", "return outer + arg0")
        )
        for normalize_locals in (False, True):
            with self.subTest(normalize_locals=normalize_locals):
                self.assertEqual(
                    self._canonical(first, normalize_local_names=normalize_locals),
                    self._canonical(second, normalize_local_names=normalize_locals),
                )

    def test_nested_defaults_resolve_in_enclosing_scope(self) -> None:
        for signature in ("y=x", "*, y=x"):
            outer = (
                "def first(x):\n"
                f"    def inner({signature}):\n"
                "        return y\n"
                "    value = inner()\n"
                "    return value\n"
            )
            external = outer.replace("y=x", "y=y")
            renamed = outer.replace("first(x)", "first(other)").replace(
                "y=x", "y=other"
            )
            for normalize_locals in (False, True):
                with self.subTest(
                    signature=signature, normalize_locals=normalize_locals
                ):
                    canonical = self._canonical(
                        outer, normalize_local_names=normalize_locals
                    )
                    self.assertNotEqual(
                        canonical,
                        self._canonical(
                            external, normalize_local_names=normalize_locals
                        ),
                    )
                    self.assertEqual(
                        canonical,
                        self._canonical(
                            renamed, normalize_local_names=normalize_locals
                        ),
                    )

    def test_shadowed_nested_parameter_has_its_own_binding(self) -> None:
        shadowed = (
            "def first(x):\n"
            "    def inner(x):\n"
            "        return x + 1\n"
            "    value = inner(2)\n"
            "    return x + value\n"
        )
        renamed = shadowed.replace("inner(x)", "inner(y)").replace(
            "return x + 1", "return y + 1"
        )
        for normalize_locals in (False, True):
            with self.subTest(normalize_locals=normalize_locals):
                self.assertEqual(
                    self._canonical(shadowed, normalize_local_names=normalize_locals),
                    self._canonical(renamed, normalize_local_names=normalize_locals),
                )

    def test_comprehension_first_iterable_uses_enclosing_scope(self) -> None:
        for expression in (
            "[x for x in x]",
            "{x for x in x}",
            "{x: x for x in x}",
            "(x for x in x)",
        ):
            with self.subTest(expression=expression):
                original = f"def f(x):\n    return {expression}\n"
                renamed_expression = expression.replace("x", "item").replace(
                    "in item", "in x"
                )
                renamed = f"def f(x):\n    return {renamed_expression}\n"
                self.assertEqual(
                    self._canonical(original, normalize_local_names=True),
                    self._canonical(renamed, normalize_local_names=True),
                )
                external = original.replace("def f(x)", "def f(values)")
                other_external = external.replace("in x", "in other")
                self.assertNotEqual(
                    self._canonical(external, normalize_local_names=True),
                    self._canonical(other_external, normalize_local_names=True),
                )

    def test_method_closure_lookup_skips_class_bindings(self) -> None:
        closure = (
            "def f(x):\n"
            "    class C:\n"
            "        x = 1\n"
            "        def method(self):\n"
            "            return x\n"
            "    return C\n"
        )
        external = closure.replace("x = 1", "y = 1").replace("return x", "return y")
        self.assertNotEqual(
            self._canonical(closure, normalize_local_names=True),
            self._canonical(external, normalize_local_names=True),
        )

    def test_method_defaults_can_reference_class_bindings(self) -> None:
        class_default = (
            "def f(x):\n"
            "    class C:\n"
            "        x = 1\n"
            "        def method(self, value=x):\n"
            "            return value\n"
            "    return C\n"
        )
        renamed_default = class_default.replace("x = 1", "y = 1").replace(
            "value=x", "value=y"
        )
        self.assertEqual(
            self._canonical(class_default, normalize_local_names=True),
            self._canonical(renamed_default, normalize_local_names=True),
        )

    def test_parameter_named_like_a_placeholder_still_matches(self) -> None:
        first = "def first(value):\n    output = transform(value)\n    return output\n"
        for name in ("arg0", "vararg", "kwarg", "v0"):
            second = (
                f"def second({name}):\n"
                f"    output = transform({name})\n"
                "    return output\n"
            )
            with self.subTest(name=name):
                self.assertEqual(
                    self._canonical(first, normalize_local_names=True),
                    self._canonical(second, normalize_local_names=True),
                )
                self.assertEqual(self._canonical(first), self._canonical(second))

    def test_parameter_names_are_ignored_at_the_identical_tier(self) -> None:
        a = "def f(x, *rest, **extra):\n    return helper(x, rest, extra)\n"
        b = "def g(y, *others, **more):\n    return helper(y, others, more)\n"
        self.assertEqual(self._canonical(a), self._canonical(b))
        c = "def h(x, *rest, **extra):\n    return other(x, rest, extra)\n"
        self.assertNotEqual(self._canonical(a), self._canonical(c))

    def test_comprehension_targets_do_not_make_external_calls_local(self) -> None:
        a = (
            "def f(items):\n"
            "    names = [format for format in items]\n"
            "    result = format(items)\n"
            "    return names, result\n"
        )
        b = a.replace("result = format(items)", "result = process(items)")
        self.assertNotEqual(
            self._canonical(a, normalize_local_names=True),
            self._canonical(b, normalize_local_names=True),
        )
        # The comprehension variable itself is still normalized.
        c = a.replace("[format for format in items]", "[entry for entry in items]")
        self.assertEqual(
            self._canonical(a, normalize_local_names=True),
            self._canonical(c, normalize_local_names=True),
        )

    def test_nested_function_locals_do_not_leak_into_the_outer_scope(self) -> None:
        a = (
            "def f(items):\n"
            "    def inner(item):\n"
            "        total = weigh(item)\n"
            "        return total\n"
            "    return total(items) + inner(items)\n"
        )
        b = a.replace("return total(items)", "return count(items)")
        self.assertNotEqual(
            self._canonical(a, normalize_local_names=True),
            self._canonical(b, normalize_local_names=True),
        )
        c = a.replace("total = weigh(item)", "acc = weigh(item)").replace(
            "        return total\n", "        return acc\n"
        )
        self.assertEqual(
            self._canonical(a, normalize_local_names=True),
            self._canonical(c, normalize_local_names=True),
        )

    def test_lambda_parameters_are_scoped_and_normalized(self) -> None:
        a = "def f(rows):\n    return sorted(rows, key=lambda row: row.score)\n"
        b = "def f(rows):\n    return sorted(rows, key=lambda r: r.score)\n"
        self.assertEqual(
            self._canonical(a, normalize_local_names=True),
            self._canonical(b, normalize_local_names=True),
        )
        # At the identical tier a lambda parameter shadowing a function
        # parameter is left alone rather than renamed as the parameter.
        c = "def f(x):\n    return apply(lambda x: x + 1, x)\n"
        rendered = self._canonical(c)
        # ast.dump formatting differs across Python versions, so check the
        # pieces rather than one exact string.
        self.assertIn("arg(arg='x')", rendered)
        self.assertIn("Name(id='«arg0»'", rendered)
        self.assertIn("Name(id='x'", rendered)

    def test_nested_global_and_nonlocal_declarations_are_respected(self) -> None:
        a = (
            "def f():\n"
            "    total = 0\n"
            "    def inner():\n"
            "        nonlocal total\n"
            "        total = total + 1\n"
            "        global counter\n"
            "        counter = counter + 1\n"
            "    inner()\n"
            "    return total\n"
        )
        rendered = self._canonical(a, normalize_local_names=True)
        self.assertIn("Name(id='counter'", rendered)
        self.assertNotIn("Name(id='total'", rendered)

    def test_bound_names_versus_all_bindings(self) -> None:
        fn = _parse_func(
            "def f(a):\n"
            "    b = [c for c in a if (d := c)]\n"
            "    e = lambda g: g\n"
            "    def inner(h):\n"
            "        i = h\n"
            "    return b, e\n"
        )
        scoped = bound_names(fn)
        self.assertEqual(scoped, frozenset({"a", "b", "d", "e", "inner"}))
        self.assertTrue({"c", "g", "h", "i"} <= all_bindings(fn))
