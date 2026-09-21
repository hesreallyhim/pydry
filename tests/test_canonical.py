"""Tests for pydry.canonical: statement tokens, triviality, and alignment."""

from __future__ import annotations

import ast
import textwrap
import unittest

from pydry.canonical import (
    bag_upper_bound,
    bound_names,
    is_trivial,
    lcs_alignment,
    sequence_similarity,
    statement_tokens,
)


def _func(src: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    module = ast.parse(textwrap.dedent(src))
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise ValueError("no function")


class StatementTokenTests(unittest.TestCase):
    def test_docstring_is_skipped_and_depth_follows_nesting(self) -> None:
        fn = _func(
            '''
            def f(items):
                """Doc."""
                out = []
                for item in items:
                    if item:
                        out.append(item)
                else:
                    out = None
                return out
            '''
        )
        tokens = statement_tokens(fn)
        self.assertEqual(
            [(t.depth, t.kind) for t in tokens],
            [
                (0, "Assign"),
                (0, "For"),
                (1, "If"),
                (2, "Expr"),
                (0, "Else"),
                (1, "Assign"),
                (0, "Return"),
            ],
        )

    def test_try_emits_handler_and_finally_markers(self) -> None:
        fn = _func(
            """
            def f():
                try:
                    run()
                except ValueError as exc:
                    log(exc)
                finally:
                    close()
            """
        )
        kinds = [(t.depth, t.kind) for t in statement_tokens(fn)]
        self.assertEqual(
            kinds,
            [
                (0, "Try"),
                (1, "Expr"),
                (0, "ExceptHandler"),
                (1, "Expr"),
                (0, "Finally"),
                (1, "Expr"),
            ],
        )

    def test_nested_function_contributes_only_a_header(self) -> None:
        fn = _func(
            """
            def outer(x):
                def inner(y):
                    a = y + 1
                    return a
                return inner(x)
            """
        )
        kinds = [t.kind for t in statement_tokens(fn)]
        self.assertEqual(kinds, ["FunctionDef", "Return"])

    def test_locals_are_renamed_but_globals_and_attributes_kept(self) -> None:
        a = _func("def f(rows):\n    total = helper(rows, CONST).value\n")
        b = _func("def g(items):\n    acc = helper(items, CONST).value\n")
        c = _func("def h(items):\n    acc = other(items, CONST).value\n")
        ta, tb, tc = (statement_tokens(fn)[0] for fn in (a, b, c))
        self.assertNotEqual(ta.raw, tb.raw)
        self.assertEqual(ta.names, tb.names)
        self.assertEqual(ta.full, tb.full)
        self.assertNotEqual(ta.full, tc.full)

    def test_constants_differ_only_at_the_full_tier(self) -> None:
        a = _func("def f(x):\n    return x * 20\n")
        b = _func("def f(x):\n    return x * 50\n")
        ta, tb = statement_tokens(a)[0], statement_tokens(b)[0]
        self.assertNotEqual(ta.names, tb.names)
        self.assertEqual(ta.full, tb.full)

    def test_loose_tier_equates_parameter_and_literal(self) -> None:
        a = _func("def clamp(v, low):\n    if v < low:\n        return low\n")
        b = _func("def unit(v):\n    if v < 0.0:\n        return 0.0\n")
        ta, tb = statement_tokens(a), statement_tokens(b)
        self.assertNotEqual(ta[0].full, tb[0].full)
        self.assertEqual(ta[0].loose, tb[0].loose)
        self.assertEqual(ta[1].loose, tb[1].loose)

    def test_loose_tier_collapses_upper_case_module_constants(self) -> None:
        a = _func("def debug(self, msg):\n    self._log(DEBUG, msg)\n")
        b = _func("def info(self, msg):\n    self._log(INFO, msg)\n")
        c = _func("def other(self, msg):\n    self._log(level(), msg)\n")
        ta, tb, tc = (statement_tokens(fn)[0] for fn in (a, b, c))
        self.assertNotEqual(ta.full, tb.full)
        self.assertEqual(ta.loose, tb.loose)
        self.assertNotEqual(ta.loose, tc.loose)

    def test_annotations_and_decorators_do_not_matter(self) -> None:
        a = _func("def f(x: int) -> int:\n    y: int = x + 1\n    return y\n")
        b = _func("def f(x):\n    y = x + 1\n    return y\n")
        self.assertEqual(
            [t.raw for t in statement_tokens(a)], [t.raw for t in statement_tokens(b)]
        )

    def test_weight_counts_calls_and_compound_headers(self) -> None:
        fn = _func("def f(x):\n    y = g(h(x))\n    if y:\n        pass\n")
        tokens = statement_tokens(fn)
        self.assertEqual([t.weight for t in tokens], [2, 1, 0])

    def test_compound_header_line_span_stops_before_body(self) -> None:
        fn = _func("def f(xs):\n    for x in xs:\n        use(x)\n        more(x)\n")
        tokens = statement_tokens(fn)
        self.assertEqual((tokens[0].lineno, tokens[0].end_lineno), (2, 2))
        self.assertEqual((tokens[2].lineno, tokens[2].end_lineno), (4, 4))


class BoundNamesTests(unittest.TestCase):
    def test_collects_every_binding_form(self) -> None:
        fn = _func(
            """
            def f(a, *args, k=1, **kw):
                b = 1
                for c in a:
                    pass
                with open(a) as d:
                    pass
                try:
                    pass
                except E as e:
                    pass
                import os as o
                [g for g in a]
                if (h := a):
                    pass
                def inner():
                    pass
                global z
                z = 3
            """
        )
        names = bound_names(fn)
        for expected in (
            "a",
            "args",
            "k",
            "kw",
            "b",
            "c",
            "d",
            "e",
            "o",
            "g",
            "h",
            "inner",
        ):
            self.assertIn(expected, names)
        self.assertNotIn("z", names)
        self.assertNotIn("open", names)


class TrivialTests(unittest.TestCase):
    def _tokens(self, body: str) -> list:  # type: ignore[type-arg]
        return statement_tokens(
            _func(f"def f(self, x):\n{textwrap.indent(body, '    ')}")
        )

    def test_stubs_and_accessors_are_trivial(self) -> None:
        self.assertTrue(is_trivial(self._tokens("pass")))
        self.assertTrue(is_trivial(self._tokens("raise NotImplementedError")))
        self.assertTrue(is_trivial(self._tokens('raise NotImplementedError("x")')))
        self.assertTrue(is_trivial(self._tokens("return self._x")))
        self.assertTrue(is_trivial(self._tokens("self._a = x\nself._b = x")))
        self.assertTrue(is_trivial(self._tokens("...")))

    def test_bodies_with_calls_or_control_flow_are_not_trivial(self) -> None:
        self.assertFalse(is_trivial(self._tokens("return helper(x)")))
        self.assertFalse(is_trivial(self._tokens("if x:\n    return 1\nreturn 2")))
        self.assertFalse(is_trivial(self._tokens("a = 1\nb = 2\nc = 3\nd = 4")))


class AlignmentTests(unittest.TestCase):
    def test_lcs_alignment_returns_index_pairs(self) -> None:
        a = [(0, "a"), (0, "b"), (0, "c"), (0, "d")]
        b = [(0, "a"), (0, "c"), (0, "x"), (0, "d")]
        self.assertEqual(lcs_alignment(a, b), [(0, 0), (2, 1), (3, 3)])
        self.assertEqual(lcs_alignment([], b), [])
        self.assertEqual(lcs_alignment(a, []), [])

    def test_sequence_similarity(self) -> None:
        self.assertEqual(sequence_similarity(0, 0, 0), 1.0)
        self.assertEqual(sequence_similarity(3, 3, 3), 1.0)
        self.assertAlmostEqual(sequence_similarity(2, 3, 3), 4 / 6)

    def test_bag_upper_bound_never_below_true_similarity(self) -> None:
        a = [(0, "a"), (0, "b"), (0, "c"), (0, "d")]
        b = [(0, "d"), (0, "c"), (0, "b"), (0, "a")]
        from collections import Counter

        bound = bag_upper_bound(Counter(a), Counter(b), len(a), len(b))
        true = sequence_similarity(len(lcs_alignment(a, b)), len(a), len(b))
        self.assertGreaterEqual(bound, true)
        self.assertEqual(bound, 1.0)


if __name__ == "__main__":
    unittest.main()


class StructuralCoverageTests(unittest.TestCase):
    def test_match_statement_tokens_and_pattern_bindings(self) -> None:
        fn = _func(
            """
            def route(command):
                match command:
                    case ["go", direction]:
                        return move(direction)
                    case {"action": action, **rest}:
                        return act(action, rest)
                    case [first, *others] if others:
                        return many(first, others)
                    case _:
                        return None
            """
        )
        tokens = statement_tokens(fn)
        self.assertEqual(tokens[0].kind, "Match")
        self.assertEqual(
            [t.kind for t in tokens if t.kind == "match_case"].__len__(), 4
        )
        self.assertEqual({t.depth for t in tokens if t.kind == "match_case"}, {1})
        self.assertEqual({t.depth for t in tokens if t.kind == "Return"}, {2})
        # Pattern-bound names are locals and get placeholders.
        joined = " ".join(t.names for t in tokens)
        for bound in ("direction", "rest", "first", "others"):
            self.assertNotIn(f"'{bound}'", joined)
        self.assertIn("'move'", joined)

    def test_try_else_marker_and_bare_string_expressions(self) -> None:
        fn = _func(
            '''
            def f():
                """Doc."""
                try:
                    run()
                except Error:
                    pass
                else:
                    "a stray string used as a comment"
                    done()
            '''
        )
        kinds = [(t.depth, t.kind) for t in statement_tokens(fn)]
        self.assertEqual(
            kinds,
            [
                (0, "Try"),
                (1, "Expr"),
                (0, "ExceptHandler"),
                (1, "Pass"),
                (0, "Else"),
                (1, "Expr"),
            ],
        )

    def test_except_star_handlers_and_cleanup_are_traversed(self) -> None:
        fn = _func(
            """
            def f():
                try:
                    run()
                except* ValueError as group:
                    recover(group)
                finally:
                    cleanup()
            """
        )
        kinds = [(t.depth, t.kind) for t in statement_tokens(fn)]
        self.assertEqual(
            kinds,
            [
                (0, "TryStar"),
                (1, "Expr"),
                (0, "ExceptHandler"),
                (1, "Expr"),
                (0, "Finally"),
                (1, "Expr"),
            ],
        )

    def test_nested_class_and_async_def_contribute_headers_only(self) -> None:
        fn = _func(
            """
            def outer(x):
                @dataclass
                class Local:
                    value: int
                    def method(self) -> int:
                        return self.value
                async def helper(y: int) -> int:
                    return await fetch(y)
                declared: int
                return Local(x), helper
            """
        )
        tokens = statement_tokens(fn)
        self.assertEqual(
            [t.kind for t in tokens],
            ["ClassDef", "AsyncFunctionDef", "AnnAssign", "Return"],
        )
        # Decorators and annotations are stripped; nested scope names are locals.
        self.assertNotIn("dataclass", tokens[0].raw)
        self.assertNotIn("'Local'", tokens[0].names)
        self.assertNotIn("'helper'", tokens[1].names)
        self.assertNotIn("annotation=Name", tokens[1].raw)

    def test_global_and_nonlocal_names_are_never_renamed(self) -> None:
        fn = _func(
            """
            def counter():
                global total
                total = total + 1
                def inner():
                    nonlocal total
                    return total
                return inner
            """
        )
        tokens = statement_tokens(fn)
        self.assertIn("Global(['total'])", tokens[0].names)
        self.assertIn("Name('total'", tokens[1].names)

    def test_docstring_only_body_is_trivial(self) -> None:
        fn = _func('def f():\n    """Only a docstring."""\n')
        tokens = statement_tokens(fn)
        self.assertEqual(tokens, [])
        self.assertTrue(is_trivial(tokens))
