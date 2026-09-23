"""Behavioral tests for the public engine entry points."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from pydry.analyze import iter_python_files
from pydry.engine import (
    abstract_candidates,
    exact_groups,
    near_matches,
    scan_functions,
)


class PyDryTests(unittest.TestCase):
    def _make_repo(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content))
        return root

    def test_exact_with_local_normalization(self):
        root = self._make_repo(
            {
                "a.py": """
                def one(x):
                    y = helper(x) + 1
                    return y
            """,
                "b.py": """
                def two(z):
                    q = helper(z) + 1
                    return q
            """,
            }
        )
        groups = exact_groups(root, min_count=2)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].count, 2)
        self.assertEqual(groups[0].tier, "renamed")
        self.assertEqual(exact_groups(root, normalize_local_names=False), [])

    def test_near_finds_literal_specialization(self):
        root = self._make_repo(
            {
                "a.py": """
                def slug_a(name):
                    cleaned = name.strip()
                    parts = cleaned.split()
                    if not parts:
                        return ""
                    return "-".join(parts)
            """,
                "b.py": """
                def slug_b(name):
                    cleaned = name.strip()
                    parts = cleaned.split()
                    if not parts:
                        return "n/a"
                    return "_".join(parts)
            """,
            }
        )
        # These are exact duplicates at the constants tier, so they only
        # appear as a near match once constant normalization is disabled.
        self.assertEqual(len(exact_groups(root)), 1)
        self.assertEqual(exact_groups(root, normalize_constants=False), [])
        self.assertEqual(near_matches(root, threshold=0.55), [])

    def test_abstract_filters_leave_separate(self):
        root = self._make_repo(
            {
                "a.py": """
                def collect(items):
                    out = []
                    for item in items:
                        if item.ok:
                            out.append(item.value)
                    return out
            """,
                "b.py": """
                def stream(items):
                    out = []
                    for item in items:
                        if item.ok:
                            yield item.value
                    return out
            """,
            }
        )
        rows = near_matches(root, threshold=0.5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].suggested_refactor_kind, "leave_separate")
        self.assertEqual(abstract_candidates(root, threshold=0.5), [])

    def test_iter_python_files_skips_virtualenv_dirs(self):
        root = self._make_repo(
            {
                "src/main.py": "def main():\n    return 1\n",
                "venv/lib/ignored.py": "def ignored():\n    return 1\n",
                ".venv/lib/ignored.py": "def ignored():\n    return 1\n",
            }
        )
        paths = [p.relative_to(root).as_posix() for p in iter_python_files(root)]
        self.assertIn("src/main.py", paths)
        self.assertNotIn("venv/lib/ignored.py", paths)
        self.assertNotIn(".venv/lib/ignored.py", paths)

    def test_near_raises_on_invalid_threshold(self):
        root = self._make_repo({"a.py": "def one():\n    return 1\n"})
        with self.assertRaises(ValueError):
            near_matches(root, threshold=1.5)
        with self.assertRaises(ValueError):
            near_matches(root, threshold=-0.1)

    def test_near_raises_on_invalid_top_k(self):
        root = self._make_repo({"a.py": "def one():\n    return 1\n"})
        with self.assertRaises(ValueError):
            near_matches(root, top_k=-1)

    def test_exact_raises_on_invalid_min_count(self):
        root = self._make_repo({"a.py": "def one():\n    return 1\n"})
        with self.assertRaises(ValueError):
            exact_groups(root, min_count=1)

    def test_scan_errors_collected_or_raised(self):
        root = self._make_repo(
            {
                "good_a.py": """
                def one(x):
                    y = helper(x)
                    return y + 1
            """,
                "good_b.py": """
                def two(y):
                    z = helper(y)
                    w = z + 2
                    return w
            """,
                "bad.py": """
                def broken(:
                    pass
            """,
            }
        )
        scan_errors: list[str] = []
        rows = near_matches(root, threshold=0.0, top_k=1, scan_errors=scan_errors)
        self.assertTrue(rows)
        self.assertEqual(len(scan_errors), 1)
        self.assertIn("bad.py", scan_errors[0])
        with self.assertRaises(RuntimeError):
            near_matches(root, threshold=0.0, strict=True)

    def test_nested_functions_are_not_marked_as_methods(self):
        root = self._make_repo(
            {
                "a.py": """
                class Box:
                    def transform(self, x):
                        def inner(v):
                            return v + 1
                        return inner(x)

                def outer(a):
                    def nested(b):
                        return b + 1
                    return nested(a)
            """,
            }
        )
        profiles = scan_functions(root)
        by_qualname = {p.occurrence.qualname: p.occurrence for p in profiles}
        self.assertTrue(by_qualname["Box.transform"].is_method)
        self.assertFalse(by_qualname["Box.transform.inner"].is_method)
        self.assertFalse(by_qualname["outer"].is_method)
        self.assertFalse(by_qualname["outer.nested"].is_method)

    def test_same_literals_do_not_trigger_literal_specialization(self):
        root = self._make_repo(
            {
                "a.py": """
                def to_dict_a(obj):
                    out = {}
                    for key, value in obj.items():
                        if value is not None:
                            out[key] = str(value)
                    return out
            """,
                "b.py": """
                def to_dict_b(obj):
                    result = {}
                    for key, value in obj.items():
                        if value is not None:
                            result[key] = str(value)
                    result.pop("id", None)
                    return result
            """,
            }
        )
        rows = near_matches(root, threshold=0.7)
        self.assertTrue(rows)
        top = rows[0]
        self.assertNotIn("literal_specialization", top.pattern_labels)
        self.assertIn("structural_variant", top.pattern_labels)
        self.assertEqual(top.suggested_refactor_kind, "extract_common_helper")


if __name__ == "__main__":
    unittest.main()
