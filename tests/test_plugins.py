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


class PluginProtocolTests(unittest.TestCase):
    def _make_repo(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, content in files.items():
            (root / name).write_text(textwrap.dedent(content))
        return root

    PAIR = {
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

    def test_plugin_may_return_none_or_override_the_suggestion(self):
        class Quiet:
            name = "quiet"

            def analyze_pair(self, ctx):
                return None

        class Opinionated:
            name = "opinionated"

            def analyze_pair(self, ctx):
                from pydry.plugins import PairPluginResult

                return PairPluginResult(suggested_refactor_kind="custom_kind")

        root = self._make_repo(self.PAIR)
        original = list(registry._pair_plugins)
        registry._pair_plugins.extend([Quiet(), Opinionated()])
        try:
            rows = near_matches(root, threshold=0.0)
        finally:
            registry._pair_plugins = original
        self.assertEqual(rows[0].suggested_refactor_kind, "custom_kind")
        self.assertIn("opinionated", rows[0].metadata)
        self.assertNotIn("quiet", rows[0].metadata)

    def test_dependency_divergence_flags_many_one_sided_module_names(self):
        root = self._make_repo(
            {
                "a.py": """
                def build(cfg):
                    a = step(cfg, alpha)
                    b = step(a, beta)
                    c = step(b, gamma)
                    d = step(c, delta)
                    e = wrap(d)
                    f = wrap(e)
                    g = wrap(f)
                    return finish(g)
                """,
                "b.py": """
                def build_other(cfg):
                    a = step(cfg, one)
                    b = step(a, two)
                    c = step(b, three)
                    d = step(c, four)
                    e = wrap(d)
                    f = wrap(e)
                    g = wrap(f)
                    return finish(g)
                """,
            }
        )
        rows = near_matches(root, threshold=0.5)
        self.assertEqual(len(rows), 1)
        self.assertIn("ambient_dependency_diff", rows[0].risk_flags)
        self.assertIn(
            "8 module-level name(s) used by only one side", rows[0].key_differences
        )
        self.assertLess(rows[0].refactorability_score, rows[0].similarity_score)
