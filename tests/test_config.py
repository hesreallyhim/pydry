from __future__ import annotations

import io
import os
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from pydry.cli import main
from pydry.config import CheckConfig, ConfigError, apply_overrides, load_check_config


class CheckConfigTests(unittest.TestCase):
    def _temporary_directory(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def _write_config(self, content: str) -> Path:
        path = self._temporary_directory() / "pydry.toml"
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_defaults_when_discovery_finds_no_pydry_toml(self):
        root = self._temporary_directory()
        original_cwd = os.getcwd()
        self.addCleanup(os.chdir, original_cwd)
        os.chdir(root)

        self.assertEqual(load_check_config(None), CheckConfig())

    def test_legacy_pyproject_configuration_is_not_discovered(self):
        root = self._temporary_directory()
        (root / "pyproject.toml").write_text(
            "[tool.pydry]\nthreshold = 0.1\n",
            encoding="utf-8",
        )
        original_cwd = os.getcwd()
        self.addCleanup(os.chdir, original_cwd)
        os.chdir(root)

        self.assertEqual(load_check_config(None), CheckConfig())

    def test_discovers_pydry_toml_and_loads_every_setting(self):
        root = self._temporary_directory()
        (root / "pydry.toml").write_text(
            textwrap.dedent(
                """
                root = "src"
                threshold = 0.91
                top_k = 37
                top_level_only = true
                strict = false
                normalize_local_names = false
                normalize_constants = false
                max_exact_groups = 1
                max_near_matches = 2
                max_abstract_candidates = 3
                fail_on_scan_errors = false
                fail_on_plugin_errors = false
                annotation_limit = 7
                """
            ),
            encoding="utf-8",
        )
        original_cwd = os.getcwd()
        self.addCleanup(os.chdir, original_cwd)
        os.chdir(root)

        self.assertEqual(
            load_check_config(None),
            CheckConfig(
                root="src",
                threshold=0.91,
                top_k=37,
                top_level_only=True,
                strict=False,
                normalize_local_names=False,
                normalize_constants=False,
                max_exact_groups=1,
                max_near_matches=2,
                max_abstract_candidates=3,
                fail_on_scan_errors=False,
                fail_on_plugin_errors=False,
                annotation_limit=7,
            ),
        )

    def test_explicit_missing_config_is_an_error(self):
        missing = self._temporary_directory() / "missing.toml"

        with self.assertRaisesRegex(ConfigError, "does not exist"):
            load_check_config(missing)

    def test_malformed_toml_is_an_error(self):
        path = self._write_config(
            """
            threshold =
            """
        )

        with self.assertRaisesRegex(ConfigError, "Invalid TOML"):
            load_check_config(path)

    def test_explicit_config_requires_canonical_filename(self):
        path = self._temporary_directory() / "pydry.ini"
        path.write_text("threshold = 0.5\n", encoding="utf-8")

        with self.assertRaisesRegex(ConfigError, "must be named pydry.toml"):
            load_check_config(path)

        alternate = self._temporary_directory() / "policy.toml"
        alternate.write_text("threshold = 0.5\n", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "must be named pydry.toml"):
            load_check_config(alternate)

    def test_pyproject_style_table_is_rejected(self):
        path = self._write_config("[tool.pydry]\nthreshold = 0.5\n")

        with self.assertRaisesRegex(ConfigError, "Unknown pydry setting.*tool"):
            load_check_config(path)

    def test_unknown_settings_are_rejected_and_sorted(self):
        path = self._write_config(
            """
            zeta = 1
            alpha = 2
            """
        )

        with self.assertRaisesRegex(
            ConfigError,
            r"Unknown pydry setting\(s\): alpha, zeta",
        ):
            load_check_config(path)

    def test_invalid_types_and_ranges_are_rejected(self):
        cases = {
            "empty root": 'root = ""',
            "non-string root": "root = 3",
            "boolean threshold": "threshold = true",
            "string threshold": 'threshold = "high"',
            "threshold below range": "threshold = -0.01",
            "threshold above range": "threshold = 1.01",
            "boolean count": "top_k = true",
            "string count": 'top_k = "many"',
            "negative count": "top_k = -1",
            "negative optional count": "max_near_matches = -1",
            "negative annotation limit": "annotation_limit = -1",
            "non-boolean flag": 'strict = "yes"',
        }
        for label, assignment in cases.items():
            with self.subTest(case=label):
                path = self._write_config(f"{assignment}\n")
                with self.assertRaises(ConfigError):
                    load_check_config(path)

    def test_numeric_threshold_is_coerced_to_float(self):
        path = self._write_config("threshold = 1\n")

        config = load_check_config(path)

        self.assertEqual(config.threshold, 1.0)
        self.assertIsInstance(config.threshold, float)

    def test_apply_overrides_ignores_none_and_replaces_supplied_values(self):
        configured = CheckConfig(root="configured", threshold=0.4, strict=False)

        effective = apply_overrides(
            configured,
            root=None,
            threshold=0.8,
            strict=True,
        )

        self.assertEqual(effective.root, "configured")
        self.assertEqual(effective.threshold, 0.8)
        self.assertTrue(effective.strict)

    def test_none_string_disables_optional_policy_limit(self):
        path = self._write_config('max_exact_groups = "none"\n')

        configured = load_check_config(path)
        overridden = apply_overrides(
            CheckConfig(max_exact_groups=3), max_exact_groups="none"
        )

        self.assertIsNone(configured.max_exact_groups)
        self.assertIsNone(overridden.max_exact_groups)

    def test_invalid_optional_policy_limit_explains_none_sentinel(self):
        path = self._write_config('max_exact_groups = "disabled"\n')

        with self.assertRaisesRegex(ConfigError, "integer or the string 'none'"):
            load_check_config(path)

    def test_cli_values_override_config_while_unspecified_values_are_preserved(self):
        root = self._temporary_directory()
        report = root / "reports" / "check.json"
        config_path = root / "config" / "pydry.toml"
        config_path.parent.mkdir()
        config_path.write_text(
            textwrap.dedent(
                """
                root = "from-config"
                threshold = 0.25
                top_k = 17
                strict = true
                normalize_constants = false
                max_exact_groups = 4
                max_abstract_candidates = 4
                """
            ),
            encoding="utf-8",
        )

        with patch("pydry.cli.run_check", return_value=0) as run:
            return_code = main(
                [
                    "check",
                    str(root),
                    "--config",
                    str(config_path),
                    "--threshold",
                    "0.75",
                    "--no-strict",
                    "--normalize-constants",
                    "--max-exact-groups",
                    "1",
                    "--max-abstract-candidates",
                    "none",
                    "--output",
                    str(report),
                    "--github",
                ]
            )

        self.assertEqual(return_code, 0)
        run.assert_called_once()
        call = run.call_args.kwargs
        effective = call["config"]
        self.assertEqual(call["root"], root)
        self.assertEqual(call["output_path"], report)
        self.assertTrue(call["github"])
        self.assertEqual(effective.root, str(root))
        self.assertEqual(effective.threshold, 0.75)
        self.assertEqual(effective.top_k, 17)
        self.assertFalse(effective.strict)
        self.assertTrue(effective.normalize_constants)
        self.assertEqual(effective.max_exact_groups, 1)
        self.assertIsNone(effective.max_abstract_candidates)

    def test_cli_discovers_pydry_toml_without_config_option(self):
        root = self._temporary_directory()
        (root / "pydry.toml").write_text(
            'root = "configured-src"\nthreshold = 0.42\n',
            encoding="utf-8",
        )
        original_cwd = os.getcwd()
        self.addCleanup(os.chdir, original_cwd)
        os.chdir(root)

        with patch("pydry.cli.run_check", return_value=0) as run:
            return_code = main(["check", "--output", "report.json"])

        self.assertEqual(return_code, 0)
        call = run.call_args.kwargs
        self.assertEqual(call["root"], Path("configured-src"))
        self.assertEqual(call["config"].threshold, 0.42)
        self.assertEqual(call["config_path"], Path("pydry.toml"))

    def test_cli_returns_two_for_invalid_configuration(self):
        path = self._write_config("unknown = true\n")
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            return_code = main(["check", "--config", str(path)])

        self.assertEqual(return_code, 2)
        self.assertIn("Unknown pydry setting", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
