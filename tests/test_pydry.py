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


class PyDupesTests(unittest.TestCase):
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
                    y = x + 1
                    return y
            """,
                "b.py": """
                def two(z):
                    q = z + 1
                    return q
            """,
            }
        )
        groups = exact_groups(root, min_count=2, normalize_local_names=True)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].count, 2)

    def test_near_finds_literal_specialization(self):
        root = self._make_repo(
            {
                "a.py": """
                def slug_a(name):
                    return normalize(name, "-")
            """,
                "b.py": """
                def slug_b(name):
                    return normalize(name, "_")
            """,
            }
        )
        rows = near_matches(root, threshold=0.55)
        self.assertTrue(rows)
        top = rows[0]
        self.assertIn("literal_specialization", top.pattern_labels)

    def test_abstract_filters_leave_separate(self):
        root = self._make_repo(
            {
                "a.py": """
                async def load():
                    return await fetch()
            """,
                "b.py": """
                def load_sync():
                    return fetch()
            """,
            }
        )
        rows = abstract_candidates(root, threshold=0.3)
        self.assertTrue(
            all(r.suggested_refactor_kind != "leave_separate" for r in rows)
        )

    def test_iter_python_files_skips_virtualenv_dirs(self):
        root = self._make_repo(
            {
                "src/main.py": """
                def keep():
                    return 1
            """,
                "venv/lib/ignored.py": """
                def drop():
                    return 2
            """,
                ".venv/lib/ignored.py": """
                def drop_too():
                    return 3
            """,
            }
        )
        paths = [p.relative_to(root).as_posix() for p in iter_python_files(root)]
        self.assertIn("src/main.py", paths)
        self.assertNotIn("venv/lib/ignored.py", paths)
        self.assertNotIn(".venv/lib/ignored.py", paths)

    def test_top_k_matches_prefix_of_full_sorted_results(self):
        root = self._make_repo(
            {
                "a.py": """
                def wrap_a(x):
                    return normalize(x)

                def wrap_b(y):
                    return normalize(y)
            """,
                "b.py": """
                def wrap_c(z):
                    return normalize(z)

                def wrap_d(w):
                    return normalize(w)
            """,
            }
        )
        full = near_matches(root, threshold=0.4)
        top = near_matches(root, threshold=0.4, top_k=3)

        def pair_key(row):
            return (row.a.qualname, row.b.qualname, row.similarity_score)

        self.assertGreaterEqual(len(full), 3)
        self.assertEqual([pair_key(r) for r in top], [pair_key(r) for r in full[:3]])

    def test_near_raises_on_invalid_threshold(self):
        root = self._make_repo({"a.py": "def one():\n    return 1\n"})
        with self.assertRaises(ValueError):
            near_matches(root, threshold=-0.1)
        with self.assertRaises(ValueError):
            near_matches(root, threshold=1.1)

    def test_near_raises_on_invalid_top_k(self):
        root = self._make_repo({"a.py": "def one():\n    return 1\n"})
        with self.assertRaises(ValueError):
            near_matches(root, threshold=0.5, top_k=-1)

    def test_exact_raises_on_invalid_min_count(self):
        root = self._make_repo({"a.py": "def one():\n    return 1\n"})
        with self.assertRaises(ValueError):
            exact_groups(root, min_count=1)

    def test_scan_errors_collected_or_raised(self):
        root = self._make_repo(
            {
                "good_a.py": """
                def one(x):
                    return x + 1
            """,
                "good_b.py": """
                def two(y):
                    return y + 1
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
        rows = scan_functions(root)
        by_qualname = {row["occurrence"].qualname: row["occurrence"] for row in rows}
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
                    return result
            """,
            }
        )
        rows = near_matches(root, threshold=0.7)
        self.assertTrue(rows)
        top = rows[0]
        self.assertNotIn("literal_specialization", top.pattern_labels)
        self.assertEqual(top.suggested_refactor_kind, "extract_common_helper")


if __name__ == "__main__":
    unittest.main()
