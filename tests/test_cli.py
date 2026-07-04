from __future__ import annotations

import io
import json
import os
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from pydry.cli import main


class CliTests(unittest.TestCase):
    def _make_repo(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content))
        return root

    def test_non_strict_reports_skipped_files(self):
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
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(["exact", str(root), "--format", "json"])
        self.assertEqual(rc, 0)
        self.assertIn("Warning: skipped 1 file(s)", stderr.getvalue())
        parsed = json.loads(stdout.getvalue())
        self.assertIsInstance(parsed, dict)
        self.assertIn("results", parsed)
        self.assertIn("diagnostics", parsed)
        self.assertEqual(parsed["diagnostics"]["scan_errors_count"], 1)

    def test_json_always_includes_diagnostics(self):
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
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(["exact", str(root), "--format", "json"])
        self.assertEqual(rc, 0)
        parsed = json.loads(stdout.getvalue())
        self.assertIsInstance(parsed, dict)
        self.assertIn("results", parsed)
        self.assertIn("diagnostics", parsed)
        diagnostics = parsed["diagnostics"]
        self.assertEqual(diagnostics["scan_errors_count"], 1)
        self.assertEqual(len(diagnostics["scan_error_samples"]), 1)
        self.assertEqual(diagnostics["plugin_errors_count"], 0)

    def test_strict_fails_on_parse_error(self):
        root = self._make_repo(
            {
                "good.py": """
                def ok():
                    return 1
            """,
                "bad.py": """
                def broken(:
                    pass
            """,
            }
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(["exact", str(root), "--strict", "--format", "json"])
        self.assertEqual(rc, 2)
        self.assertIn("Error: Failed to parse/read", stderr.getvalue())

    def test_invalid_threshold_exits(self):
        root = self._make_repo({"ok.py": "def x():\n    return 1\n"})
        with self.assertRaises(SystemExit) as ctx:
            main(["near", str(root), "--threshold", "1.5"])
        self.assertEqual(ctx.exception.code, 2)

    def test_showcase_json_output_shape(self):
        root = self._make_repo(
            {
                "a.py": """
                def normalize_items(values):
                    out = []
                    for value in values:
                        if value > 0:
                            out.append(value)
                    return out

                def build_user_query(user_id):
                    query = "SELECT * FROM users WHERE id = ?"
                    return db.execute(query, user_id)
            """,
                "b.py": """
                def normalize_entries(items):
                    out = []
                    for item in items:
                        if item > 0:
                            out.append(item)
                    return out

                def build_admin_query(admin_id):
                    query = "SELECT * FROM admins WHERE id = ?"
                    return db.execute(query, admin_id)
            """,
            }
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(["showcase", str(root), "--format", "json", "--top-k", "2"])
        self.assertEqual(rc, 0)
        parsed = json.loads(stdout.getvalue())
        self.assertIn("results", parsed)
        self.assertIn("diagnostics", parsed)
        results = parsed["results"]
        self.assertIn("summary", results)
        self.assertIn("top_examples", results)
        summary = results["summary"]
        self.assertIn("exact_group_count", summary)
        self.assertIn("near_count", summary)
        self.assertIn("abstract_count", summary)

    def test_showcase_uses_current_directory_by_default(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            original_cwd = os.getcwd()
            self.addCleanup(os.chdir, original_cwd)
            os.chdir(tmp_dir)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = main(["showcase"])
            self.assertEqual(rc, 0)
            self.assertIn("PYDRY SHOWCASE SIMULATION", stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")

    def test_simulate_alias_uses_showcase_pipeline(self):
        root = self._make_repo(
            {
                "a.py": """
                def render_user_name(name):
                    return f"User: {name}"
            """,
                "b.py": """
                def render_admin_name(name):
                    return f"Admin: {name}"
            """,
            }
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(["simulate", str(root), "--format", "json", "--top-k", "1"])
        self.assertEqual(rc, 0)
        parsed = json.loads(stdout.getvalue())
        self.assertIn("results", parsed)
        self.assertIn("summary", parsed["results"])

    def test_output_requires_json_format(self):
        root = self._make_repo({"a.py": "def x():\n    return 1\n"})
        out_path = root / "report.json"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(["near", str(root), "--output", str(out_path)])
        self.assertEqual(rc, 2)
        self.assertFalse(out_path.exists())
        self.assertIn("--output requires --format json", stderr.getvalue())

    def test_output_writes_json_file_and_suppresses_stdout(self):
        root = self._make_repo(
            {
                "a.py": """
                def alpha(value):
                    return value + 1
            """,
                "b.py": """
                def beta(value):
                    return value + 1
            """,
            }
        )
        out_path = root / "out" / "near.json"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(
                [
                    "near",
                    str(root),
                    "--format",
                    "json",
                    "--output",
                    str(out_path),
                ]
            )
        self.assertEqual(rc, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Wrote JSON report to", stderr.getvalue())
        self.assertTrue(out_path.exists())
        parsed = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertIn("results", parsed)
        self.assertIn("diagnostics", parsed)

    def test_report_returns_aggregated_sections(self):
        root = self._make_repo(
            {
                "a.py": """
                def normalize_items(values):
                    out = []
                    for value in values:
                        if value > 0:
                            out.append(value)
                    return out
            """,
                "b.py": """
                def normalize_entries(items):
                    out = []
                    for item in items:
                        if item > 0:
                            out.append(item)
                    return out
            """,
            }
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(["report", str(root)])
        self.assertEqual(rc, 0)
        parsed = json.loads(stdout.getvalue())
        self.assertIn("results", parsed)
        results = parsed["results"]
        self.assertIn("summary", results)
        self.assertIn("exact", results)
        self.assertIn("near", results)
        self.assertIn("abstract", results)


class CliTextOutputTests(unittest.TestCase):
    """Tests for text-format output paths in CLI subcommands."""

    def _make_repo(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content))
        return root

    def _make_dup_repo(self) -> Path:
        """Create a repo with known exact duplicates and near matches."""
        return self._make_repo(
            {
                "a.py": """
                def normalize_items(values):
                    out = []
                    for value in values:
                        if value > 0:
                            out.append(value)
                    return out

                def build_user_query(user_id):
                    query = "SELECT * FROM users WHERE id = ?"
                    return db.execute(query, user_id)
            """,
                "b.py": """
                def normalize_entries(items):
                    out = []
                    for item in items:
                        if item > 0:
                            out.append(item)
                    return out

                def build_admin_query(admin_id):
                    query = "SELECT * FROM admins WHERE id = ?"
                    return db.execute(query, admin_id)
            """,
            }
        )

    # ── Argument validators ─────────────────────────────────────

    def test_parse_min_count_valid(self) -> None:
        from pydry.cli import _parse_min_count

        self.assertEqual(_parse_min_count("2"), 2)
        self.assertEqual(_parse_min_count("10"), 10)

    def test_parse_min_count_too_low(self) -> None:
        import argparse

        from pydry.cli import _parse_min_count

        with self.assertRaises(argparse.ArgumentTypeError):
            _parse_min_count("1")
        with self.assertRaises(argparse.ArgumentTypeError):
            _parse_min_count("0")

    def test_parse_min_count_negative(self) -> None:
        import argparse

        from pydry.cli import _parse_min_count

        with self.assertRaises(argparse.ArgumentTypeError):
            _parse_min_count("-1")

    # ── Exact text output ───────────────────────────────────────

    def test_exact_text_format_with_duplicates(self) -> None:
        root = self._make_dup_repo()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(
                [
                    "exact",
                    str(root),
                    "--normalize-local-names",
                    "--normalize-constants",
                    "--format",
                    "text",
                ]
            )
        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn("Group", output)
        self.assertIn("occurrences", output)
        self.assertIn("hash=", output)

    def test_exact_text_no_duplicates(self) -> None:
        root = self._make_repo(
            {
                "a.py": """
                def totally_unique(x):
                    return x + 1
            """,
                "b.py": """
                def also_unique(x, y):
                    return x * y + 2
            """,
            }
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(["exact", str(root), "--format", "text"])
        self.assertEqual(rc, 0)
        self.assertIn("No duplicate functions found", stdout.getvalue())

    # ── Near text output ────────────────────────────────────────

    def test_near_text_format_with_matches(self) -> None:
        root = self._make_dup_repo()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(["near", str(root), "--threshold", "0.5", "--format", "text"])
        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn("sim=", output)
        self.assertIn("refactor=", output)
        self.assertIn("<->", output)

    def test_near_text_no_matches(self) -> None:
        root = self._make_repo(
            {
                "a.py": """
                def unique_func():
                    return 42
            """,
            }
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(["near", str(root), "--threshold", "0.99", "--format", "text"])
        self.assertEqual(rc, 0)
        self.assertIn("No near matches found", stdout.getvalue())

    # ── Abstract text output ────────────────────────────────────

    def test_abstract_text_format_with_candidates(self) -> None:
        root = self._make_dup_repo()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(
                [
                    "abstract",
                    str(root),
                    "--threshold",
                    "0.5",
                    "--format",
                    "text",
                ]
            )
        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        # Abstract uses _print_near under the hood
        self.assertIn("sim=", output)

    def test_abstract_text_no_candidates(self) -> None:
        root = self._make_repo(
            {
                "a.py": """
                def unique():
                    return 1
            """,
            }
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(
                [
                    "abstract",
                    str(root),
                    "--threshold",
                    "0.99",
                    "--format",
                    "text",
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn("No near matches found", stdout.getvalue())

    # ── Showcase text output ────────────────────────────────────

    def test_showcase_text_format(self) -> None:
        root = self._make_dup_repo()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(
                [
                    "showcase",
                    str(root),
                    "--format",
                    "text",
                    "--top-k",
                    "2",
                ]
            )
        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn("PYDRY SHOWCASE SIMULATION", output)
        self.assertIn("[1/3]", output)
        self.assertIn("[2/3]", output)
        self.assertIn("[3/3]", output)
        self.assertIn("Tip:", output)

    def test_showcase_text_empty_corpus(self) -> None:
        root = self._make_repo(
            {
                "a.py": """
                def unique():
                    return 1
            """,
            }
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(
                [
                    "showcase",
                    str(root),
                    "--format",
                    "text",
                    "--threshold",
                    "0.99",
                ]
            )
        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn("PYDRY SHOWCASE SIMULATION", output)
        self.assertIn("none", output)

    # ── Diagnostics printing ────────────────────────────────────

    def test_plugin_errors_printed_to_stderr(self) -> None:
        """Plugin errors are printed to stderr when they occur in text mode."""
        root = self._make_repo(
            {
                "a.py": """
                def add_one(x):
                    return x + 1
            """,
                "b.py": """
                def add_two(y):
                    return y + 1
            """,
            }
        )
        # Add a broken plugin temporarily
        from pydry.plugins import registry

        class BrokenPlugin:
            name = "broken_test_cli"

            def analyze_pair(self, ctx: object) -> object:
                raise RuntimeError("intentional cli test failure")

        original_plugins = list(registry._pair_plugins)
        registry._pair_plugins.append(BrokenPlugin())  # type: ignore[arg-type]
        try:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                rc = main(
                    [
                        "near",
                        str(root),
                        "--threshold",
                        "0.0",
                        "--format",
                        "text",
                    ]
                )
        finally:
            registry._pair_plugins = original_plugins
        self.assertEqual(rc, 0)
        err = stderr.getvalue()
        self.assertIn("plugin error", err.lower())

    def test_scan_errors_more_than_five(self) -> None:
        """When there are >5 scan errors, the '... N more' message appears."""
        files = {}
        for i in range(8):
            files[f"bad_{i}.py"] = f"def broken{i}(:\n    pass\n"
        files["good_a.py"] = "def a(x):\n    return x + 1\n"
        files["good_b.py"] = "def b(y):\n    return y + 1\n"
        root = self._make_repo(files)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(["near", str(root), "--threshold", "0.0", "--format", "text"])
        self.assertEqual(rc, 0)
        err = stderr.getvalue()
        self.assertIn("more", err)

    # ── Strict error path ───────────────────────────────────────

    def test_near_strict_fails_on_parse_error(self) -> None:
        root = self._make_repo(
            {
                "good.py": "def ok():\n    return 1\n",
                "bad.py": "def broken(:\n    pass\n",
            }
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(
                [
                    "near",
                    str(root),
                    "--strict",
                    "--format",
                    "text",
                ]
            )
        self.assertEqual(rc, 2)
        self.assertIn("Error:", stderr.getvalue())

    def test_abstract_strict_fails_on_parse_error(self) -> None:
        root = self._make_repo(
            {
                "good.py": "def ok():\n    return 1\n",
                "bad.py": "def broken(:\n    pass\n",
            }
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(
                [
                    "abstract",
                    str(root),
                    "--strict",
                    "--format",
                    "text",
                ]
            )
        self.assertEqual(rc, 2)

    def test_showcase_strict_fails_on_parse_error(self) -> None:
        root = self._make_repo(
            {
                "good.py": "def ok():\n    return 1\n",
                "bad.py": "def broken(:\n    pass\n",
            }
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(
                [
                    "showcase",
                    str(root),
                    "--strict",
                    "--format",
                    "text",
                ]
            )
        self.assertEqual(rc, 2)

    def test_report_strict_fails_on_parse_error(self) -> None:
        root = self._make_repo(
            {
                "good.py": "def ok():\n    return 1\n",
                "bad.py": "def broken(:\n    pass\n",
            }
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(
                [
                    "report",
                    str(root),
                    "--strict",
                ]
            )
        self.assertEqual(rc, 2)

    # ── Output validation path ──────────────────────────────────

    def test_exact_output_requires_json_format(self) -> None:
        root = self._make_repo({"a.py": "def x():\n    return 1\n"})
        out_path = root / "report.json"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(["exact", str(root), "--output", str(out_path)])
        self.assertEqual(rc, 2)
        self.assertIn("--output requires --format json", stderr.getvalue())

    def test_abstract_output_requires_json_format(self) -> None:
        root = self._make_repo({"a.py": "def x():\n    return 1\n"})
        out_path = root / "report.json"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(["abstract", str(root), "--output", str(out_path)])
        self.assertEqual(rc, 2)
        self.assertIn("--output requires --format json", stderr.getvalue())

    def test_showcase_output_requires_json_format(self) -> None:
        root = self._make_repo({"a.py": "def x():\n    return 1\n"})
        out_path = root / "report.json"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(["showcase", str(root), "--output", str(out_path)])
        self.assertEqual(rc, 2)
        self.assertIn("--output requires --format json", stderr.getvalue())

    def test_report_output_writes_file(self) -> None:
        root = self._make_dup_repo()
        out_path = root / "out" / "report.json"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = main(
                [
                    "report",
                    str(root),
                    "--output",
                    str(out_path),
                ]
            )
        self.assertEqual(rc, 0)
        self.assertTrue(out_path.exists())
        parsed = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertIn("results", parsed)


if __name__ == "__main__":
    unittest.main()
