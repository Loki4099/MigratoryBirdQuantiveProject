import os
import unittest
from unittest.mock import patch

from style_rotation.config.settings import Settings, get_settings


class SettingsTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_defaults_target_local_postgres(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)  # type: ignore[call-arg]
        self.assertEqual(settings.system_version, "0.22.0")
        self.assertEqual(settings.cell_result_directory, "artifacts/cell_result_payloads")
        self.assertEqual(settings.signal_export_directory, "artifacts/signal_research_exports")
        self.assertTrue(settings.database_url.startswith("postgresql+psycopg://"))

    def test_environment_overrides_are_validated(self) -> None:
        with patch.dict(
            os.environ,
            {
                "STYLE_ROTATION_SYSTEM_VERSION": "0.1.1",
                "STYLE_ROTATION_LOG_LEVEL": "DEBUG",
                "STYLE_ROTATION_CELL_RESULT_DIRECTORY": "D:/shared/cell-results",
            },
            clear=True,
        ):
            settings = Settings(_env_file=None)  # type: ignore[call-arg]
        self.assertEqual(settings.system_version, "0.1.1")
        self.assertEqual(settings.log_level, "DEBUG")
        self.assertEqual(settings.cell_result_directory, "D:/shared/cell-results")


if __name__ == "__main__":
    unittest.main()
