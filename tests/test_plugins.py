from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from pydry.engine import near_matches
from pydry.plugins import registry


class PluginTests(unittest.TestCase):
    def _make_repo(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content))
        return root

    def test_builtin_wrapper_plugin_labels_pair(self):
        root = self._make_repo(
            {
                "a.py": """
                def wrap_a(x):
                    return normalize(x)
            """,
                "b.py": """
                def wrap_b(y):
                    return normalize(y)
            """,
            }
        )
        rows = near_matches(root, threshold=0.4)
        self.assertTrue(rows)
        top = rows[0]
        self.assertIn("wrapper", top.pattern_labels)
        self.assertEqual(
            top.suggested_refactor_kind, "merge_into_single_function_with_param"
        )
        self.assertIn("wrapper", top.metadata)

    def test_plugin_failure_is_isolated_and_reported(self):
        root = self._make_repo(
            {
                "a.py": """
                def add_one(x):
                    return x + 1
            """,
                "b.py": """
                def add_two(y):
                    return y + 2
            """,
            }
        )

        class BrokenPlugin:
            name = "broken_test_plugin"

            def analyze_pair(self, ctx):
                raise RuntimeError("intentional plugin failure")

        original_plugins = list(registry._pair_plugins)
        registry._pair_plugins.append(BrokenPlugin())
        try:
            plugin_errors: list[str] = []
            rows = near_matches(
                root,
                threshold=0.0,
                top_k=1,
                plugin_errors=plugin_errors,
            )
        finally:
            registry._pair_plugins = original_plugins

        self.assertTrue(rows)
        self.assertTrue(plugin_errors)
        self.assertIn("broken_test_plugin", plugin_errors[0])
        self.assertIn("_plugin_errors", rows[0].metadata)


if __name__ == "__main__":
    unittest.main()
