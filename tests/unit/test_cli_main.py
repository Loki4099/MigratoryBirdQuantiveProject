import json
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, date, datetime
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from style_rotation.api.actor_context import ActorRoleDenied
from style_rotation.cli.main import _recovery_release_transition, main, run
from style_rotation.config.settings import Settings


class UnifiedCliTests(unittest.TestCase):
    def test_modules_json_reports_all_boundaries(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            result = main(["modules", "--json"])
        payload = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(len(payload), 11)
        self.assertEqual(payload[0]["key"], "catalog")
        self.assertIn("workspace", {item["key"] for item in payload})
        self.assertIn("product", {item["key"] for item in payload})

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

    def test_joint_restore_requires_database_and_object_bundle_identity(self) -> None:
        backup_id = "00000000-0000-0000-0000-000000000001"
        with patch("style_rotation.cli.main._backup_restore_joint", return_value=0) as command:
            result = main(
                [
                    "backup",
                    "restore-joint",
                    "--backup-record-id",
                    backup_id,
                    "--docker-service",
                    "postgres",
                    "--bundle-root",
                    "D:/bird-backups/v022-objects",
                    "--restored-object-root",
                    "D:/bird-restore/v022-objects",
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(
            backup_id,
            "postgres",
            "D:/bird-backups/v022-objects",
            "D:/bird-restore/v022-objects",
            None,
        )

    def test_backup_command_fails_closed_without_operator_role(self) -> None:
        settings = Settings(
            _env_file=None,
            api_actor_key="researcher-only",
            api_operator_enabled=False,
        )
        with (
            patch("style_rotation.cli.main.get_settings", return_value=settings),
            patch("style_rotation.cli.main.BackupService") as service,
            pytest.raises(ActorRoleDenied, match="operator"),
        ):
            main(
                [
                    "backup",
                    "create",
                    "--output",
                    "artifacts/v02.dump",
                    "--git-commit",
                    "abcdef0",
                ]
            )
        service.assert_not_called()

    def test_restore_evidence_command_requires_explicit_store_and_interval(self) -> None:
        backup_id = "00000000-0000-0000-0000-000000000001"
        with patch(
            "style_rotation.cli.main._recovery_publish_restore_evidence", return_value=0
        ) as command:
            result = main(
                [
                    "recovery",
                    "publish-restore-evidence",
                    "--backup-record-id",
                    backup_id,
                    "--restored-object-root",
                    "artifacts/restored-payloads",
                    "--started-at",
                    "2026-08-11T10:00:00+00:00",
                    "--completed-at",
                    "2026-08-11T10:15:00+00:00",
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(
            backup_id,
            "artifacts/restored-payloads",
            datetime(2026, 8, 11, 10, tzinfo=UTC),
            datetime(2026, 8, 11, 10, 15, tzinfo=UTC),
        )

    def test_rollback_evidence_command_requires_pinned_probe_identity(self) -> None:
        transition_id = "00000000-0000-0000-0000-000000000001"
        artifact_id = "00000000-0000-0000-0000-000000000002"
        idempotency_key = "00000000-0000-0000-0000-000000000003"
        with patch(
            "style_rotation.cli.main._recovery_publish_rollback_evidence", return_value=0
        ) as command:
            result = main(
                [
                    "recovery",
                    "publish-rollback-evidence",
                    "--rollback-transition-artifact-id",
                    transition_id,
                    "--v021-artifact-id",
                    artifact_id,
                    "--replay-command-name",
                    "save_workspace_draft",
                    "--replay-idempotency-key",
                    idempotency_key,
                    "--replay-request-file",
                    "artifacts/pinned-request.json",
                    "--completed-at",
                    "2026-08-12T08:30:00+00:00",
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(
            transition_id,
            artifact_id,
            "save_workspace_draft",
            idempotency_key,
            "artifacts/pinned-request.json",
            datetime(2026, 8, 12, 8, 30, tzinfo=UTC),
        )

    def test_release_preflight_collects_evidence_without_transitioning(self) -> None:
        artifact_id = "00000000-0000-0000-0000-000000000001"
        with patch(
            "style_rotation.cli.main._recovery_release_preflight", return_value=2
        ) as command:
            result = main(
                [
                    "recovery",
                    "release-preflight",
                    "--target",
                    "default",
                    "--evidence",
                    f"parity_gate_artifact_id={artifact_id}",
                ]
            )
        self.assertEqual(result, 2)
        command.assert_called_once_with(
            "default",
            (f"parity_gate_artifact_id={artifact_id}",),
            None,
        )

    def test_release_transition_requires_explicit_reason_and_evidence(self) -> None:
        artifact_id = "00000000-0000-0000-0000-000000000001"
        with patch(
            "style_rotation.cli.main._recovery_release_transition", return_value=0
        ) as command:
            result = main(
                [
                    "recovery",
                    "release-transition",
                    "--target",
                    "shadow",
                    "--reason-code",
                    "begin_shadow",
                    "--reason",
                    "start controlled dual run",
                    "--evidence",
                    f"shadow_plan_artifact_id={artifact_id}",
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(
            "shadow",
            "begin_shadow",
            "start controlled dual run",
            (f"shadow_plan_artifact_id={artifact_id}",),
            None,
        )

    def test_release_transition_does_not_mutate_when_preflight_is_blocked(self) -> None:
        settings = Settings(_env_file=None, api_actor_key="operator", api_operator_enabled=True)
        with (
            patch("style_rotation.cli.main.get_settings", return_value=settings),
            patch("style_rotation.cli.main.create_postgres_engine", return_value=object()),
            patch("style_rotation.cli.main.ReleaseControlService") as service_type,
            pytest.raises(ValueError, match="preflight blocked"),
        ):
            service_type.return_value.preflight.return_value = SimpleNamespace(
                ready=False,
                blocker_details=("missing default evidence",),
            )
            _recovery_release_transition(
                "default",
                "cutover",
                "must not publish",
                (),
                None,
            )
        service_type.return_value.transition.assert_not_called()

    def test_storage_retention_dry_run_is_explicitly_read_only(self) -> None:
        suite_id = "00000000-0000-0000-0000-000000000001"
        with patch(
            "style_rotation.cli.main._storage_retention_dry_run", return_value=0
        ) as command:
            result = main(
                [
                    "storage",
                    "retention-dry-run",
                    "--cache-ttl-days",
                    "21",
                    "--cache-quota-gib",
                    "8",
                    "--retain-suite-id",
                    suite_id,
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(21, 8, (suite_id,))

    def test_idempotency_pending_audit_is_available_without_running_commands(self) -> None:
        with patch("style_rotation.cli.main._idempotency_pending", return_value=0) as command:
            result = main(["idempotency", "audit-pending", "--limit", "25"])
        self.assertEqual(result, 0)
        command.assert_called_once_with(25)

    def test_idempotency_response_repair_requires_explicit_audit_confirmation(self) -> None:
        command_id = "00000000-0000-0000-0000-000000000001"
        with patch(
            "style_rotation.cli.main._idempotency_repair_response", return_value=0
        ) as command:
            result = main(
                [
                    "idempotency",
                    "repair-response",
                    "--command-name",
                    "promote_experiment_result",
                    "--idempotency-key",
                    command_id,
                    "--confirm-request-fingerprint",
                    "a" * 64,
                    "--response-file",
                    "audited-response.json",
                    "--confirm-outcome-audited",
                    "OUTCOME_AUDITED",
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(
            "promote_experiment_result",
            command_id,
            "a" * 64,
            "audited-response.json",
            "OUTCOME_AUDITED",
        )

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
                    "experiment",
                    "run-release-suite",
                    "--target-engine-artifact-id",
                    engine_id,
                    "--expected-target-count",
                    "630",
                    "--git-commit",
                    "abcdef0",
                    "--as-of",
                    "2026-08-03",
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with(
            (),
            engine_id,
            630,
            "abcdef0",
            "requirements.lock",
            date(2026, 8, 3),
            None,
            None,
            "v02_formal_release",
            1,
            253,
            False,
            1,
        )

    def test_version_flag_uses_v02_package_version(self) -> None:
        output = StringIO()
        with self.assertRaisesRegex(SystemExit, "0"), redirect_stdout(output):
            main(["--version"])
        self.assertEqual(output.getvalue().strip(), "style-rotation 0.22.0")

    def test_v021_experiment_worker_supports_persistent_recovery_mode(self) -> None:
        with patch(
            "style_rotation.cli.main._experiment_run_v021_worker", return_value=0
        ) as command:
            result = main(
                [
                    "experiment",
                    "run-v021-worker",
                    "--worker-id",
                    "recovery-worker",
                    "--forever",
                    "--poll-seconds",
                    "0.25",
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with("recovery-worker", 1, True, 0.25)

    def test_v021_monitoring_worker_supports_persistent_recovery_mode(self) -> None:
        with patch(
            "style_rotation.cli.main._product_run_v021_monitoring_worker", return_value=0
        ) as command:
            result = main(
                [
                    "experiment",
                    "run-v021-monitoring-worker",
                    "--forever",
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with("v021-monitoring-worker", 1, True, 1.0)

    def test_signal_export_worker_supports_persistent_recovery_mode(self) -> None:
        with patch(
            "style_rotation.cli.main._signal_run_research_export_worker", return_value=0
        ) as command:
            result = main(
                [
                    "experiment",
                    "run-signal-export-worker",
                    "--worker-id",
                    "export-recovery-worker",
                    "--forever",
                    "--poll-seconds",
                    "0.5",
                ]
            )
        self.assertEqual(result, 0)
        command.assert_called_once_with("export-recovery-worker", 1, True, 0.5)

    def test_v022_shadow_worker_is_available_from_the_primary_cli(self) -> None:
        with patch(
            "style_rotation.cli.main._run_v022_shadow_worker", return_value=0
        ) as command:
            result = main(
                [
                    "experiment",
                    "run-v022-shadow-worker",
                    "--v021-compiler-version",
                    "compiler-21.9",
                    "--v021-executor-version",
                    "executor-21.9",
                    "--v021-environment-fingerprint",
                    "1" * 64,
                    "--v021-capability-key",
                    "legacy-product",
                    "--v022-compiler-version",
                    "compiler-22.0",
                    "--v022-executor-version",
                    "executor-22.0",
                    "--v022-environment-fingerprint",
                    "2" * 64,
                    "--v022-capability-key",
                    "v022-product",
                    "--forever",
                ]
            )

        self.assertEqual(result, 0)
        args = command.call_args.args[0]
        self.assertEqual(args.v021_capability_key, "legacy-product")
        self.assertEqual(args.v022_capability_key, "v022-product")
        self.assertTrue(args.forever)

    def test_v022_operations_probes_default_to_read_only_observation(self) -> None:
        with patch(
            "style_rotation.cli.main._operations_collect_v022_probes", return_value=0
        ) as command:
            result = main(
                [
                    "operations",
                    "collect-v022-probes",
                    "--window-start-at",
                    "2026-08-01T00:00:00+08:00",
                    "--window-end-at",
                    "2026-08-08T00:00:00+08:00",
                ]
            )

        self.assertEqual(result, 0)
        self.assertFalse(command.call_args.args[2])

    def test_v022_operations_readiness_requires_explicit_measurements(self) -> None:
        policy = str(uuid.uuid4())
        measurements = (str(uuid.uuid4()), str(uuid.uuid4()))
        with patch(
            "style_rotation.cli.main._operations_publish_v022_readiness", return_value=0
        ) as command:
            result = main(
                [
                    "operations",
                    "publish-v022-readiness",
                    "--slo-policy-version-id",
                    policy,
                    "--window-start-at",
                    "2026-08-01T00:00:00+08:00",
                    "--window-end-at",
                    "2026-08-08T00:00:00+08:00",
                    "--measurement-id",
                    measurements[0],
                    "--measurement-id",
                    measurements[1],
                ]
            )

        self.assertEqual(result, 0)
        self.assertEqual(command.call_args.args[0], policy)
        self.assertEqual(command.call_args.args[3], measurements)

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
