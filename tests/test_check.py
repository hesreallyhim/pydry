from __future__ import annotations

import io
import json
import os
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pydry.check import PolicyViolation, evaluate_policy, run_check
from pydry.config import CheckConfig
from pydry.github import annotation, finding_annotations
from pydry.models import ExactGroup, FunctionOccurrence


class CheckCommandTests(unittest.TestCase):
    def _make_repo(self, files: dict[str, str] | None = None) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name, content in (files or {}).items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content), encoding="utf-8")
        return root

    def _run(
        self,
        root: Path,
        config: CheckConfig,
        report: Path,
        *,
        github: bool = False,
        config_path: Path | None = None,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            return_code = run_check(
                root=root,
                config=config,
                output_path=report,
                github=github,
                config_path=config_path,
            )
        return return_code, stdout.getvalue(), stderr.getvalue()

    def test_pass_returns_zero_and_writes_report(self):
        root = self._make_repo()
        report = root / "artifacts" / "pydry.json"

        return_code, stdout, stderr = self._run(root, CheckConfig(), report)

        self.assertEqual(return_code, 0)
        self.assertIn("pydry check: passed", stdout)
        self.assertEqual(stderr, "")
        self.assertTrue(report.is_file())
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertTrue(payload["results"]["check"]["passed"])

    def test_policy_failure_returns_one_and_report_has_canonical_shape(self):
        root = self._make_repo(
            {
                "a.py": """
                def first(value):
                    result = normalize(value) + 1
                    return result
                """,
                "b.py": """
                def second(item):
                    output = normalize(item) + 1
                    return output
                """,
            }
        )
        report = root / "pydry-report.json"
        config = CheckConfig(
            strict=False,
            max_exact_groups=0,
            max_near_matches=None,
            max_abstract_candidates=None,
        )

        return_code, stdout, stderr = self._run(root, config, report)

        self.assertEqual(return_code, 1)
        self.assertIn("pydry check: failed", stdout)
        self.assertIn("policy allows 0", stderr)
        self.assertTrue(report.is_file(), "policy failures must retain their report")
        envelope = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(set(envelope), {"results", "diagnostics"})
        results = envelope["results"]
        self.assertEqual(
            set(results),
            {
                "root",
                "config",
                "settings",
                "summary",
                "baseline",
                "check",
                "exact",
                "near",
                "abstract",
                "blocks",
            },
        )
        self.assertEqual(
            set(results["check"]),
            {"passed", "limits", "violations", "report_truncated"},
        )
        self.assertFalse(results["check"]["passed"])
        self.assertEqual(
            results["check"]["report_truncated"],
            {"near": False, "abstract": False, "blocks": False},
        )
        self.assertEqual(results["summary"]["exact_group_count"], 1)
        self.assertEqual(
            set(results["exact"][0]),
            {
                "hash",
                "count",
                "occurrences",
                "tier",
                "stmt_count",
                "savings",
                "canonical",
                "baselined",
            },
        )
        self.assertEqual(
            set(envelope["diagnostics"]),
            {
                "scan_errors_count",
                "scan_error_samples",
                "plugin_errors_count",
                "plugin_error_samples",
            },
        )

    def test_invalid_root_returns_two_without_writing_report(self):
        root = self._make_repo() / "missing"
        report = root.parent / "report.json"

        return_code, _, stderr = self._run(root, CheckConfig(), report)

        self.assertEqual(return_code, 2)
        self.assertIn("Invalid directory", stderr)
        self.assertFalse(report.exists())

    def test_report_write_error_returns_two(self):
        root = self._make_repo()

        return_code, _, stderr = self._run(root, CheckConfig(), root)

        self.assertEqual(return_code, 2)
        self.assertIn("Could not write report", stderr)

    def test_output_path_with_line_break_is_rejected(self):
        root = self._make_repo()
        report = root / "bad\noutput.json"

        return_code, _, stderr = self._run(root, CheckConfig(), report)

        self.assertEqual(return_code, 2)
        self.assertIn("line breaks are not allowed", stderr)
        self.assertFalse(report.exists())

    def test_analysis_error_returns_two_without_writing_report(self):
        root = self._make_repo()
        report = root / "report.json"

        with patch("pydry.check.exact_groups", side_effect=RuntimeError("boom")):
            return_code, _, stderr = self._run(root, CheckConfig(), report)

        self.assertEqual(return_code, 2)
        self.assertIn("Error: boom", stderr)
        self.assertFalse(report.exists())

    def test_strict_scan_error_is_execution_failure(self):
        root = self._make_repo({"broken.py": "def broken(:\n    pass\n"})
        report = root / "strict.json"

        return_code, _, stderr = self._run(
            root,
            CheckConfig(strict=True),
            report,
        )

        self.assertEqual(return_code, 2)
        self.assertIn("Failed to parse/read", stderr)
        self.assertFalse(report.exists())

    def test_lenient_scan_errors_follow_configured_policy(self):
        root = self._make_repo({"broken.py": "def broken(:\n    pass\n"})
        failing_report = root / "failing.json"
        passing_report = root / "passing.json"
        common = {
            "strict": False,
            "max_exact_groups": None,
            "max_near_matches": None,
            "max_abstract_candidates": None,
        }

        failed, _, _ = self._run(
            root,
            CheckConfig(fail_on_scan_errors=True, **common),
            failing_report,
        )
        passed, _, _ = self._run(
            root,
            CheckConfig(fail_on_scan_errors=False, **common),
            passing_report,
        )

        self.assertEqual(failed, 1)
        self.assertEqual(passed, 0)
        failed_payload = json.loads(failing_report.read_text(encoding="utf-8"))
        passed_payload = json.loads(passing_report.read_text(encoding="utf-8"))
        self.assertEqual(failed_payload["diagnostics"]["scan_errors_count"], 1)
        self.assertEqual(passed_payload["diagnostics"]["scan_errors_count"], 1)
        self.assertEqual(
            [
                item["category"]
                for item in failed_payload["results"]["check"]["violations"]
            ],
            ["scan_errors"],
        )
        self.assertTrue(passed_payload["results"]["check"]["passed"])

    def test_evaluate_policy_enforces_counts_and_diagnostic_preferences(self):
        config = CheckConfig(
            max_exact_groups=1,
            max_near_matches=None,
            max_abstract_candidates=0,
            fail_on_scan_errors=False,
            fail_on_plugin_errors=True,
        )

        violations = evaluate_policy(
            config=config,
            exact_count=2,
            near_count=999,
            abstract_count=1,
            scan_errors=["ignored scan error"],
            plugin_errors=["plugin failed"],
        )

        self.assertEqual(
            [item.category for item in violations],
            ["exact_groups", "abstract_candidates", "plugin_errors"],
        )
        self.assertEqual([item.actual for item in violations], [2, 1, 1])
        self.assertEqual([item.allowed for item in violations], [1, 0, 0])

    def test_github_annotation_escapes_commands_and_properties(self):
        occurrence = FunctionOccurrence(
            path="src/a:b,c%file.py\nnext",
            lineno=4,
            end_lineno=6,
            col_offset=2,
            name="example",
            qualname="example",
            kind="function",
            param_count=0,
            is_method=False,
        )
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            annotation(
                "error",
                "bad%message\r\nnext",
                occurrence,
                title="pydry: check,failed%",
            )

        self.assertEqual(
            stdout.getvalue(),
            "::error title=pydry%3A check%2Cfailed%25,"
            "file=src/a%3Ab%2Cc%25file.py%0Anext,line=4,col=3,endLine=6::"
            "bad%25message%0D%0Anext\n",
        )

    def test_finding_annotation_limit_caps_emitted_annotations(self):
        occurrences = [
            self._occurrence(f"function_{index}", index) for index in range(3)
        ]
        exact = ExactGroup(
            hash="hash",
            count=3,
            occurrences=occurrences,
        )
        violation = PolicyViolation(
            category="exact_groups",
            actual=1,
            allowed=0,
            message="too many exact groups",
        )
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            emitted = finding_annotations(
                config=CheckConfig(annotation_limit=1),
                violations=[violation],
                exact_rows=[exact],
                near_rows=[],
                abstract_rows=[],
            )

        lines = stdout.getvalue().splitlines()
        self.assertEqual(emitted, 1)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("::error "))

    def test_github_outputs_and_step_summary_use_environment_paths(self):
        root = self._make_repo()
        report = root / "reports" / "pydry.json"
        github_output = root / "github-output.txt"
        step_summary = root / "step-summary.md"

        with patch.dict(
            os.environ,
            {
                "GITHUB_OUTPUT": str(github_output),
                "GITHUB_STEP_SUMMARY": str(step_summary),
            },
            clear=True,
        ):
            return_code, _, _ = self._run(
                root,
                CheckConfig(),
                report,
                github=True,
                config_path=Path("config/pydry policy.toml"),
            )

        self.assertEqual(return_code, 0)
        self.assertEqual(
            github_output.read_text(encoding="utf-8").splitlines(),
            [
                "result=pass",
                f"report={report}",
                "exact-groups=0",
                "near-matches=0",
                "abstract-candidates=0",
                "block-clones=0",
            ],
        )
        summary = step_summary.read_text(encoding="utf-8")
        self.assertIn("## pydry check: Passed", summary)
        self.assertIn("| Exact duplicate groups | 0 | 0 | 0 |", summary)
        self.assertIn(f"Report: `{report}`", summary)
        self.assertIn(
            f"pydry check {root} --config 'config/pydry policy.toml'",
            summary,
        )
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(payload["results"]["config"], "config/pydry policy.toml")

    def test_github_metadata_write_error_returns_two(self):
        root = self._make_repo()
        report = root / "report.json"
        missing_output = root / "missing" / "github-output.txt"

        with patch.dict(
            os.environ,
            {"GITHUB_OUTPUT": str(missing_output)},
            clear=True,
        ):
            return_code, _, stderr = self._run(
                root,
                CheckConfig(),
                report,
                github=True,
            )

        self.assertEqual(return_code, 2)
        self.assertIn("Could not write GitHub metadata", stderr)
        self.assertTrue(report.exists())

    @staticmethod
    def _occurrence(name: str, lineno: int) -> FunctionOccurrence:
        return FunctionOccurrence(
            path=f"src/{name}.py",
            lineno=lineno,
            end_lineno=lineno + 1,
            col_offset=0,
            name=name,
            qualname=name,
            kind="function",
            param_count=0,
            is_method=False,
        )


if __name__ == "__main__":
    unittest.main()


class GitHubRenderingTests(unittest.TestCase):
    """Annotations and the job summary for every finding category."""

    def _make_repo(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        loader = """
        def load_{name}(path):
            with open(path) as fh:
                raw = fh.read()
            rows = raw.splitlines()
            out = []
            for row in rows:
                if not row.strip():
                    continue
                parts = row.split(",")
                rec = {{"id": int(parts[0]), "name": parts[1].strip()}}
                out.append(rec)
            out.sort(key=lambda r: r["id"])
            {tail}
        """
        (root / "a.py").write_text(
            textwrap.dedent(loader.format(name="users", tail="return out")),
            encoding="utf-8",
        )
        (root / "b.py").write_text(
            textwrap.dedent(loader.format(name="orders", tail="return finish(out)")),
            encoding="utf-8",
        )
        (root / "c.py").write_text(
            textwrap.dedent(loader.format(name="items", tail="return out")),
            encoding="utf-8",
        )
        (root / "d.py").write_text(
            textwrap.dedent(
                """
                def pipeline(path):
                    with open(path) as fh:
                        raw = fh.read()
                    rows = raw.splitlines()
                    out = []
                    for row in rows:
                        if not row.strip():
                            continue
                        parts = row.split(",")
                        rec = {"id": int(parts[0]), "name": parts[1].strip()}
                        out.append(rec)
                    out.sort(key=lambda r: r["id"])
                    stats = {}
                    for rec in out:
                        stats[rec["id"]] = len(rec["name"])
                    keys = sorted(stats)
                    hist = {}
                    for k in keys:
                        hist[stats[k]] = hist.get(stats[k], 0) + 1
                    report = []
                    for k in keys:
                        report.append(f"{k}: {stats[k]}")
                    print("\\n".join(report))
                    summary = {"count": len(out), "hist": hist}
                    if summary["count"] == 0:
                        summary["empty"] = True
                    return summary
                """
            ),
            encoding="utf-8",
        )
        return root

    def test_every_category_annotates_and_summarizes(self) -> None:
        root = self._make_repo()
        report = root / "report.json"
        github_output = root / "out.txt"
        step_summary = root / "summary.md"
        config = CheckConfig(
            strict=False,
            threshold=0.8,
            max_exact_groups=0,
            max_block_clones=0,
            max_near_matches=0,
            max_abstract_candidates=0,
            exclude=("ignored",),
            annotation_limit=50,
        )
        stdout = io.StringIO()
        with (
            patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(github_output),
                    "GITHUB_STEP_SUMMARY": str(step_summary),
                },
                clear=True,
            ),
            redirect_stdout(stdout),
            redirect_stderr(io.StringIO()),
        ):
            code = run_check(
                root=root,
                config=config,
                output_path=report,
                github=True,
                baseline_path=root / "absent.json",
            )
        self.assertEqual(code, 1)
        out = stdout.getvalue()
        self.assertIn("::error title=pydry,file=", out)
        self.assertIn("Exact duplicate group (2 occurrences, identical)", out)
        self.assertIn("Repeated block of", out)
        self.assertIn("Near match: load_users and load_orders", out)
        self.assertIn("Abstraction candidate: load_users and load_orders", out)
        # Block annotations have no column; function annotations do.
        block_lines = [line for line in out.splitlines() if "Repeated block" in line]
        self.assertTrue(block_lines)
        self.assertNotIn(",col=", block_lines[0])
        self.assertIn("endLine=", block_lines[0])
        summary = step_summary.read_text(encoding="utf-8")
        self.assertIn("### Policy violations", summary)
        self.assertIn("--exclude ignored", summary)
        self.assertIn("| Repeated blocks | 1 | 1 | 0 |", summary)
        self.assertIn("block-clones=1", github_output.read_text(encoding="utf-8"))

    def test_summary_mentions_an_applied_baseline(self) -> None:
        root = self._make_repo()
        report = root / "report.json"
        baseline = root / "baseline.json"
        step_summary = root / "summary.md"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            run_check(
                root=root,
                config=CheckConfig(strict=False),
                output_path=report,
                github=False,
                baseline_path=baseline,
                update_baseline=True,
            )
        with (
            patch.dict(
                os.environ, {"GITHUB_STEP_SUMMARY": str(step_summary)}, clear=True
            ),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            code = run_check(
                root=root,
                config=CheckConfig(strict=False),
                output_path=report,
                github=True,
                baseline_path=baseline,
            )
        self.assertEqual(code, 0)
        summary = step_summary.read_text(encoding="utf-8")
        self.assertIn(f"Baseline: `{baseline}`", summary)
        self.assertIn(f"--baseline {baseline}", summary)

    def test_analysis_failure_in_github_mode_emits_an_error_annotation(self) -> None:
        root = self._make_repo()
        (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
        stdout = io.StringIO()
        with (
            patch.dict(os.environ, {}, clear=True),
            redirect_stdout(stdout),
            redirect_stderr(io.StringIO()),
        ):
            code = run_check(
                root=root,
                config=CheckConfig(strict=True),
                output_path=root / "report.json",
                github=True,
            )
        self.assertEqual(code, 2)
        self.assertIn("::error title=pydry analysis failed::", stdout.getvalue())

    def test_lenient_scan_errors_become_warning_annotations(self) -> None:
        root = self._make_repo()
        (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
        stdout = io.StringIO()
        with (
            patch.dict(os.environ, {}, clear=True),
            redirect_stdout(stdout),
            redirect_stderr(io.StringIO()),
        ):
            code = run_check(
                root=root,
                config=CheckConfig(
                    strict=False,
                    fail_on_scan_errors=False,
                    max_exact_groups=None,
                    max_block_clones=None,
                ),
                output_path=root / "report.json",
                github=True,
            )
        self.assertEqual(code, 0)
        self.assertIn("::warning title=pydry scan error::", stdout.getvalue())
