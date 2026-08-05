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

    def test_backup_create_exposes_custom_dump_workflow(self) -> None:
        with patch("style_rotation.cli.main._backup_create", return_value=0) as command:
            result = main(
                [
                    "backup",
                    "create",
                    "--output",
                    "artifacts/v02.dump",
                    "--git-commit",
                    "abcdef0",
                    "--docker-service",
                    "postgres-test",
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with("artifacts/v02.dump", "abcdef0", "postgres-test")

    def test_experiment_publish_gross_requires_both_artifacts(self) -> None:
        ids = [f"00000000-0000-0000-0000-00000000000{index}" for index in range(1, 3)]
        with patch("style_rotation.cli.main._experiment_publish_gross", return_value=0) as command:
            result = main(
                [
                    "experiment",
                    "publish-gross",
                    "--target-path-artifact-id",
                    ids[0],
                    "--accounting-engine-artifact-id",
                    ids[1],
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(*ids)

    def test_experiment_publish_net_requires_gross_and_cost_scenario(self) -> None:
        ids = [f"00000000-0000-0000-0000-00000000000{index}" for index in range(1, 3)]
        with patch("style_rotation.cli.main._experiment_publish_net", return_value=0) as command:
            result = main(
                [
                    "experiment",
                    "publish-net",
                    "--gross-path-artifact-id",
                    ids[0],
                    "--cost-scenario-artifact-id",
                    ids[1],
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(*ids)

    def test_experiment_publish_benchmark_target_requires_complete_identity(self) -> None:
        ids = [f"00000000-0000-0000-0000-00000000000{index}" for index in range(1, 4)]
        with patch(
            "style_rotation.cli.main._experiment_publish_benchmark_target", return_value=0
        ) as command:
            result = main(
                [
                    "experiment",
                    "publish-benchmark-target",
                    "--reference-target-artifact-id",
                    ids[0],
                    "--benchmark-version-artifact-id",
                    ids[1],
                    "--benchmark-engine-artifact-id",
                    ids[2],
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(*ids)

    def test_experiment_release_cell_exposes_complete_recovery_path(self) -> None:
        target_id = "00000000-0000-0000-0000-000000000001"
        with patch(
            "style_rotation.cli.main._experiment_run_release_cell", return_value=0
        ) as command:
            result = main(
                [
                    "experiment",
                    "run-release-cell",
                    "--target-path-artifact-id",
                    target_id,
                    "--git-commit",
                    "abcdef0",
                    "--as-of",
                    "2026-08-03",
                    "--interval",
                    "trailing_3_years",
                    "--cost-bps",
                    "10",
                    "--suite-key",
                    "v02_release_weekly",
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(
            target_id,
            "abcdef0",
            "requirements.lock",
            date(2026, 8, 3),
            "trailing_3_years",
            10,
            "v02_release_weekly",
            1,
            253,
        )

    def test_experiment_release_suite_defaults_to_formal_matrix(self) -> None:
        target_ids = [
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        ]
        with patch(
            "style_rotation.cli.main._experiment_run_release_suite", return_value=0
        ) as command:
            result = main(
                [
                    "experiment",
                    "run-release-suite",
                    "--target-path-artifact-id",
                    target_ids[0],
                    "--target-path-artifact-id",
                    target_ids[1],
                    "--git-commit",
                    "abcdef0",
                    "--as-of",
                    "2026-08-03",
                    "--suite-key",
                    "v02_formal",
                    "--defer-cohorts",
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(
            tuple(target_ids),
            None,
            None,
            "abcdef0",
            "requirements.lock",
            date(2026, 8, 3),
            None,
            None,
            "v02_formal",
            1,
            253,
            True,
            1,
        )

    def test_experiment_release_suite_can_select_a_guarded_target_engine_grid(self) -> None:
        engine_id = "00000000-0000-0000-0000-000000000099"
        with patch(
            "style_rotation.cli.main._experiment_run_release_suite", return_value=0
        ) as command:
            result = main(
                [
                    "experiment", "run-release-suite",
                    "--target-engine-artifact-id", engine_id,
                    "--expected-target-count", "630",
                    "--git-commit", "abcdef0",
                    "--as-of", "2026-08-03",
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(
            (), engine_id, 630, "abcdef0", "requirements.lock", date(2026, 8, 3),
            None, None, "v02_formal_release", 1, 253, False, 1,
        )

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

    def test_forward_return_publish_requires_exact_context_and_dates(self) -> None:
        ids = [f"00000000-0000-0000-0000-00000000000{index}" for index in range(1, 5)]
        with patch("style_rotation.cli.main._forward_return_publish", return_value=0) as command:
            result = main(
                [
                    "data",
                    "publish-forward-returns",
                    "--catalog-artifact-id",
                    ids[0],
                    "--universe-artifact-id",
                    ids[1],
                    "--bundle-artifact-id",
                    ids[2],
                    "--engine-artifact-id",
                    ids[3],
                    "--start",
                    "2020-01-01",
                    "--end",
                    "2025-12-31",
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(*ids, date(2020, 1, 1), date(2025, 12, 31))

    def test_factor_bootstrap_uses_explicit_catalog_file(self) -> None:
        with patch("style_rotation.cli.main._factor_bootstrap", return_value=0) as command:
            result = main(["factor", "bootstrap", "--catalog-file", "factor.json"])
        self.assertEqual(result, 0)
        command.assert_called_once_with("factor.json")

    def test_signal_bootstrap_uses_explicit_catalog_file(self) -> None:
        with patch("style_rotation.cli.main._signal_bootstrap", return_value=0) as command:
            result = main(["signal", "bootstrap", "--catalog-file", "signal.json"])
        self.assertEqual(result, 0)
        command.assert_called_once_with("signal.json")

    def test_model_bootstrap_uses_explicit_catalog_file(self) -> None:
        with patch("style_rotation.cli.main._model_bootstrap", return_value=0) as command:
            result = main(["model", "bootstrap", "--catalog-file", "model.json"])
        self.assertEqual(result, 0)
        command.assert_called_once_with("model.json")

    def test_strategy_bootstrap_uses_explicit_catalog_file(self) -> None:
        with patch("style_rotation.cli.main._strategy_bootstrap", return_value=0) as command:
            result = main(["strategy", "bootstrap", "--catalog-file", "strategy.json"])
        self.assertEqual(result, 0)
        command.assert_called_once_with("strategy.json")

    def test_strategy_product_requires_complete_product_identity(self) -> None:
        ids = [f"00000000-0000-0000-0000-00000000000{index}" for index in range(1, 4)]
        with patch("style_rotation.cli.main._strategy_publish_product", return_value=0) as command:
            result = main(
                [
                    "strategy",
                    "publish-product",
                    "--strategy-catalog-artifact-id",
                    ids[0],
                    "--model-catalog-artifact-id",
                    ids[1],
                    "--universe-artifact-id",
                    ids[2],
                    "--model-specification-key",
                    "dimension_equal_weight__momentum_trend",
                    "--strategy-variant-key",
                    "top_k_equal_weight__k2",
                    "--schedule-key",
                    "weekly_last_common_session_close",
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(
            *ids,
            "dimension_equal_weight__momentum_trend",
            "top_k_equal_weight__k2",
            "weekly_last_common_session_close",
        )

    def test_strategy_target_publish_accepts_optional_auxiliary_dataset(self) -> None:
        ids = [f"00000000-0000-0000-0000-00000000000{index}" for index in range(1, 5)]
        with patch("style_rotation.cli.main._strategy_publish_target", return_value=0) as command:
            result = main(
                [
                    "strategy",
                    "publish-target",
                    "--product-artifact-id",
                    ids[0],
                    "--model-dataset-artifact-id",
                    ids[1],
                    "--target-engine-artifact-id",
                    ids[2],
                    "--auxiliary-signal-dataset-artifact-id",
                    ids[3],
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(*ids)

    def test_model_engine_command_requires_explicit_commit(self) -> None:
        with patch("style_rotation.cli.main._model_bootstrap_engine", return_value=0) as command:
            result = main(
                [
                    "model",
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

    def test_model_publish_requires_exact_catalog_context_and_engines(self) -> None:
        ids = [f"00000000-0000-0000-0000-00000000000{index}" for index in range(1, 7)]
        with patch("style_rotation.cli.main._model_publish", return_value=0) as command:
            result = main(
                [
                    "model",
                    "publish",
                    "--model-catalog-artifact-id",
                    ids[0],
                    "--signal-catalog-artifact-id",
                    ids[1],
                    "--bundle-artifact-id",
                    ids[2],
                    "--eligibility-artifact-id",
                    ids[3],
                    "--signal-engine-artifact-id",
                    ids[4],
                    "--model-engine-artifact-id",
                    ids[5],
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(*ids)

    def test_signal_engine_command_requires_explicit_commit(self) -> None:
        with patch("style_rotation.cli.main._signal_bootstrap_engine", return_value=0) as command:
            result = main(
                [
                    "signal",
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

    def test_signal_publish_requires_exact_upstream_context(self) -> None:
        ids = [f"00000000-0000-0000-0000-00000000000{index}" for index in range(1, 7)]
        with patch("style_rotation.cli.main._signal_publish", return_value=0) as command:
            result = main(
                [
                    "signal",
                    "publish",
                    "--signal-catalog-artifact-id",
                    ids[0],
                    "--factor-catalog-artifact-id",
                    ids[1],
                    "--bundle-artifact-id",
                    ids[2],
                    "--eligibility-artifact-id",
                    ids[3],
                    "--factor-engine-artifact-id",
                    ids[4],
                    "--signal-engine-artifact-id",
                    ids[5],
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(*ids)

    def test_signal_evaluate_requires_target_and_both_engines(self) -> None:
        ids = [f"00000000-0000-0000-0000-00000000000{index}" for index in range(1, 5)]
        with patch("style_rotation.cli.main._signal_evaluate", return_value=0) as command:
            result = main(
                [
                    "signal",
                    "evaluate",
                    "--signal-catalog-artifact-id",
                    ids[0],
                    "--forward-return-artifact-id",
                    ids[1],
                    "--signal-engine-artifact-id",
                    ids[2],
                    "--evaluation-engine-artifact-id",
                    ids[3],
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(*ids)

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
