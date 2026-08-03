import unittest

from style_rotation.persistence.base import SCHEMA_NAMES
from style_rotation.persistence.database import head_revisions, validate_reset_target

LOCAL_TEST_URL = (
    "postgresql+psycopg://style_rotation:style_rotation@localhost:55432/style_rotation_test"
)


class DatabaseFoundationUnitTests(unittest.TestCase):
    def test_schema_boundary_contains_exactly_nine_domains(self) -> None:
        self.assertEqual(len(SCHEMA_NAMES), 9)
        self.assertEqual(
            set(SCHEMA_NAMES),
            {
                "catalog",
                "data",
                "factor",
                "signal",
                "model",
                "strategy",
                "experiment",
                "lineage",
                "ops",
            },
        )

    def test_clean_v02_migration_has_one_head(self) -> None:
        self.assertEqual(head_revisions(LOCAL_TEST_URL), ("20260803_10_v02_signal_core",))

    def test_reset_requires_exact_local_project_database_confirmation(self) -> None:
        self.assertEqual(
            validate_reset_target(LOCAL_TEST_URL, "style_rotation_test", "test"),
            "style_rotation_test",
        )
        invalid_cases = (
            (LOCAL_TEST_URL, "wrong", "test"),
            (LOCAL_TEST_URL, "style_rotation_test", "production"),
            (
                "postgresql+psycopg://user:pass@remote.example/research",
                "research",
                "test",
            ),
            ("postgresql+psycopg://user:pass@localhost/postgres", "postgres", "test"),
            ("postgresql+psycopg://user:pass@localhost/other", "other", "test"),
        )
        for database_url, confirmation, environment in invalid_cases:
            with (
                self.subTest(database_url=database_url, environment=environment),
                self.assertRaises(ValueError),
            ):
                validate_reset_target(database_url, confirmation, environment)


if __name__ == "__main__":
    unittest.main()
