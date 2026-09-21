"""Tests for baselines, profiles, and exclusions in the check command."""

from __future__ import annotations

import io
import json
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from pydry.baseline import load_baseline
from pydry.check import run_check
from pydry.cli import main
from pydry.config import CheckConfig, ConfigError, apply_overrides, load_check_config

DUPLICATE = """
def first(value):
    result = normalize(value) + 1
    return result
"""

OTHER = """
def second(item):
    output = normalize(item) + 1
    return output
"""

THIRD = """
def third(thing):
    payload = normalize(thing) + 1
    return payload
"""


class BaselineTests(unittest.TestCase):
    def _make_repo(self, files: dict[str, str]) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content), encoding="utf-8")
        return root

    def _run(self, root: Path, **kwargs: object) -> tuple[int, str, str, dict]:  # type: ignore[type-arg]
        report = root / "report.json"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = run_check(
                root=root,
                config=CheckConfig(strict=False),
                output_path=report,
                github=False,
                **kwargs,  # type: ignore[arg-type]
            )
        payload = (
            json.loads(report.read_text(encoding="utf-8"))["results"]
            if report.is_file()
            else {}
        )
        return code, stdout.getvalue(), stderr.getvalue(), payload

    def test_update_baseline_accepts_current_findings_and_passes(self) -> None:
        root = self._make_repo({"a.py": DUPLICATE, "b.py": OTHER})
        baseline = root / ".pydry-baseline.json"

        code, stdout, _, payload = self._run(
            root, baseline_path=baseline, update_baseline=True
        )

        self.assertEqual(code, 0)
        self.assertIn(f"Baseline written to {baseline}", stdout)
        recorded = load_baseline(baseline)
        self.assertEqual(len(recorded.exact), 1)
        self.assertTrue(payload["baseline"]["applied"])
        self.assertEqual(payload["baseline"]["new"]["exact"], 0)
        self.assertTrue(payload["exact"][0]["baselined"])

    def test_existing_baseline_suppresses_known_findings_only(self) -> None:
        root = self._make_repo({"a.py": DUPLICATE, "b.py": OTHER})
        baseline = root / ".pydry-baseline.json"
        self._run(root, baseline_path=baseline, update_baseline=True)

        code, _, _, _ = self._run(root, baseline_path=baseline)
        self.assertEqual(code, 0)

        (root / "c.py").write_text(
            textwrap.dedent(
                """
                def alpha(x):
                    value = transform(x)
                    if value:
                        return value * 2
                    return None

                def beta(y):
                    result = transform(y)
                    if result:
                        return result * 2
                    return None
                """
            )
        )
        code, stdout, stderr, payload = self._run(root, baseline_path=baseline)
        self.assertEqual(code, 1)
        self.assertIn("new: exact=1", stdout)
        self.assertIn("policy allows 0", stderr)
        self.assertEqual(payload["summary"]["exact_group_count"], 2)
        self.assertEqual(payload["baseline"]["new"]["exact"], 1)
        flags = sorted(group["baselined"] for group in payload["exact"])
        self.assertEqual(flags, [False, True])

    def test_missing_baseline_warns_and_evaluates_everything(self) -> None:
        root = self._make_repo({"a.py": DUPLICATE, "b.py": OTHER})
        code, _, stderr, payload = self._run(root, baseline_path=root / "absent.json")
        self.assertEqual(code, 1)
        self.assertIn("does not exist", stderr)
        self.assertFalse(payload["baseline"]["applied"])

    def test_malformed_baseline_is_an_execution_failure(self) -> None:
        root = self._make_repo({"a.py": DUPLICATE, "b.py": OTHER})
        baseline = root / "baseline.json"
        baseline.write_text('{"version": 99}', encoding="utf-8")
        code, _, stderr, _ = self._run(root, baseline_path=baseline)
        self.assertEqual(code, 2)
        self.assertIn("Unsupported baseline format", stderr)

    def test_cli_wires_baseline_flags(self) -> None:
        root = self._make_repo({"a.py": DUPLICATE, "b.py": OTHER})
        baseline = root / "accepted.json"
        report = root / "report.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            first = main(
                [
                    "check",
                    str(root),
                    "--no-strict",
                    "--baseline",
                    str(baseline),
                    "--update-baseline",
                    "--output",
                    str(report),
                ]
            )
            second = main(
                [
                    "check",
                    str(root),
                    "--no-strict",
                    "--baseline",
                    str(baseline),
                    "--output",
                    str(report),
                ]
            )
            third = main(["check", str(root), "--no-strict", "--output", str(report)])
        self.assertEqual((first, second, third), (0, 0, 1))


class ProfileAndExclusionTests(unittest.TestCase):
    def _make_repo(self, files: dict[str, str]) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content), encoding="utf-8")
        return root

    def test_profiles_set_defaults_that_explicit_keys_override(self) -> None:
        root = self._make_repo(
            {
                "pydry.toml": """
                profile = "lenient"
                threshold = 0.9
                """
            }
        )
        config = load_check_config(root / "pydry.toml")
        self.assertEqual(config.profile, "lenient")
        self.assertEqual(config.threshold, 0.9)
        self.assertEqual(config.min_statements, 4)
        self.assertIsNone(config.max_block_clones)

        strict = apply_overrides(CheckConfig(), profile="strict")
        self.assertEqual(strict.max_abstract_candidates, 0)
        self.assertEqual(strict.block_min_statements, 5)

        with self.assertRaises(ConfigError):
            apply_overrides(CheckConfig(), profile="unknown")

    def test_exclude_and_baseline_keys_are_validated(self) -> None:
        root = self._make_repo(
            {
                "pydry.toml": """
                exclude = ["tests", "**/generated_*.py"]
                baseline = ".pydry-baseline.json"
                """
            }
        )
        config = load_check_config(root / "pydry.toml")
        self.assertEqual(config.exclude, ("tests", "**/generated_*.py"))
        self.assertEqual(config.baseline, ".pydry-baseline.json")

        (root / "pydry.toml").write_text('exclude = "tests"\n', encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_check_config(root / "pydry.toml")
        (root / "pydry.toml").write_text("baseline = 3\n", encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_check_config(root / "pydry.toml")

    def test_excluded_paths_are_not_scanned(self) -> None:
        root = self._make_repo(
            {
                "src/a.py": DUPLICATE,
                "tests/test_a.py": OTHER,
                "src/gen/b.py": THIRD,
            }
        )
        report = root / "report.json"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = run_check(
                root=root,
                config=CheckConfig(strict=False, exclude=("tests", "src/gen")),
                output_path=report,
                github=False,
            )
        self.assertEqual(code, 0)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = run_check(
                root=root,
                config=CheckConfig(strict=False, exclude=("tests",)),
                output_path=report,
                github=False,
            )
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
