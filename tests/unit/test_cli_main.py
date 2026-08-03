import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import StringIO
from unittest.mock import patch

from style_rotation.cli.main import main, run


class UnifiedCliTests(unittest.TestCase):
    def test_modules_json_reports_all_boundaries(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            result = main(["modules", "--json"])
        payload = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(len(payload), 9)
        self.assertEqual(payload[0]["key"], "catalog")

    def test_next_planned_command_fails_explicitly(self) -> None:
        error = StringIO()
        with redirect_stderr(error):
            result = main(["signal"])
        self.assertEqual(result, 2)
        self.assertIn("not implemented", error.getvalue())
        self.assertIn("M4", error.getvalue())

    def test_version_flag_uses_v02_package_version(self) -> None:
        output = StringIO()
        with self.assertRaisesRegex(SystemExit, "0"), redirect_stdout(output):
            main(["--version"])
        self.assertEqual(output.getvalue().strip(), "style-rotation 0.2.0")

    def test_console_entry_converts_validation_error_to_clean_exit(self) -> None:
        error = StringIO()
        with (
            patch("style_rotation.cli.main.main", side_effect=ValueError("unsafe target")),
            redirect_stderr(error),
            self.assertRaisesRegex(SystemExit, "2"),
        ):
            run()
        self.assertEqual(error.getvalue().strip(), "error: unsafe target")

    def test_data_commands_parse_iso_dates(self) -> None:
        with patch("style_rotation.cli.main._data_calendar", return_value=0) as command:
            result = main(
                [
                    "data",
                    "calendar",
                    "--start",
                    "2026-01-01",
                    "--end",
                    "2026-12-31",
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(date(2026, 1, 1), date(2026, 12, 31), 1)

    def test_factor_bootstrap_uses_explicit_catalog_file(self) -> None:
        with patch("style_rotation.cli.main._factor_bootstrap", return_value=0) as command:
            result = main(["factor", "bootstrap", "--catalog-file", "factor.json"])
        self.assertEqual(result, 0)
        command.assert_called_once_with("factor.json")

    def test_factor_engine_command_requires_explicit_commit(self) -> None:
        with patch("style_rotation.cli.main._factor_bootstrap_engine", return_value=0) as command:
            result = main(
                [
                    "factor",
                    "bootstrap-engine",
                    "--git-commit",
                    "abcdef0",
                    "--dependency-lock-file",
                    "lock.txt",
                    "--version",
                    "2",
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with("abcdef0", "lock.txt", 2)

    def test_factor_publish_requires_all_lineage_artifacts(self) -> None:
        ids = [f"00000000-0000-0000-0000-00000000000{index}" for index in range(1, 5)]
        with patch("style_rotation.cli.main._factor_publish", return_value=0) as command:
            result = main(
                [
                    "factor",
                    "publish",
                    "--factor-catalog-artifact-id",
                    ids[0],
                    "--bundle-artifact-id",
                    ids[1],
                    "--eligibility-artifact-id",
                    ids[2],
                    "--engine-artifact-id",
                    ids[3],
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(*ids)

    def test_factor_diagnose_requires_calculation_and_diagnostic_engines(self) -> None:
        ids = [f"00000000-0000-0000-0000-00000000000{index}" for index in range(1, 6)]
        with patch("style_rotation.cli.main._factor_diagnose", return_value=0) as command:
            result = main(
                [
                    "factor",
                    "diagnose",
                    "--factor-catalog-artifact-id",
                    ids[0],
                    "--bundle-artifact-id",
                    ids[1],
                    "--eligibility-artifact-id",
                    ids[2],
                    "--factor-engine-artifact-id",
                    ids[3],
                    "--diagnostic-engine-artifact-id",
                    ids[4],
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(*ids)


if __name__ == "__main__":
    unittest.main()
