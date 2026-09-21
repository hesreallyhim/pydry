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

    def test_side_effect_and_dependency_plugins_flag_risks(self):
        root = self._make_repo(
            {
                "a.py": """
                def save_txt(report, path):
                    lines = []
                    for key, value in report.items():
                        lines.append(f"{key}: {value}")
                    content = "\\n".join(lines)
                    with open(path, "w") as f:
                        f.write(content)
            """,
                "b.py": """
                def save_csv(report, path):
                    lines = [",".join(report.keys())]
                    lines.append(",".join(str(v) for v in report.values()))
                    content = "\\n".join(lines)
                    with open(path, "w") as f:
                        f.write(content)
            """,
            }
        )
        rows = near_matches(root, threshold=0.5)
        self.assertEqual(len(rows), 1)
        self.assertIn("possible_side_effects", rows[0].risk_flags)
        self.assertEqual(rows[0].metadata["side_effects"]["calls"], ["f.write"])

    def test_plugin_failure_is_isolated_and_reported(self):
        root = self._make_repo(
            {
                "a.py": """
                def add_one(x):
                    y = helper(x)
                    return y + 1
            """,
                "b.py": """
                def add_two(y):
                    z = helper(y)
                    w = z + 2
                    return w
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
