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

    def test_planned_command_fails_explicitly(self) -> None:
        error = StringIO()
        with redirect_stderr(error):
            result = main(["factor"])
        self.assertEqual(result, 2)
        self.assertIn("not implemented", error.getvalue())
        self.assertIn("M3", error.getvalue())

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


if __name__ == "__main__":
    unittest.main()
