import tomllib
import unittest
from pathlib import Path

import style_rotation
from style_rotation.architecture import DOMAIN_BOUNDARIES, validate_domain_boundaries


class ArchitectureTests(unittest.TestCase):
    def test_nine_domain_boundaries_are_unique_and_acyclic(self) -> None:
        validate_domain_boundaries()
        self.assertEqual(len(DOMAIN_BOUNDARIES), 9)
        self.assertEqual(len({item.key for item in DOMAIN_BOUNDARIES}), 9)

    def test_expected_research_chain_dependencies_are_explicit(self) -> None:
        by_key = {item.key: item for item in DOMAIN_BOUNDARIES}
        self.assertIn("factor", by_key["signal"].upstream)
        self.assertIn("signal", by_key["model"].upstream)
        self.assertIn("model", by_key["strategy"].upstream)
        self.assertIn("strategy", by_key["experiment"].upstream)

    def test_package_and_project_versions_match(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        with (project_root / "pyproject.toml").open("rb") as handle:
            pyproject = tomllib.load(handle)
        self.assertEqual(style_rotation.__version__, "0.2.0")
        self.assertEqual(pyproject["project"]["version"], style_rotation.__version__)


if __name__ == "__main__":
    unittest.main()
