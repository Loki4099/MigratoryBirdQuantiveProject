from __future__ import annotations

import uuid
from datetime import date
from typing import Any, cast

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.suite_runtime_commands import (
    BACK_ADJUSTED_RESEARCH_SEMANTICS,
    RESEARCH_DATA_BUNDLE_KEY,
    SuiteRuntimeCommandService,
    SuiteRuntimeSubmission,
    _benchmark_input,
    _bind_evaluation_cohort,
    _default_evaluation_cohort,
    _next_suite_run_binding_ordinal,
    _public_status,
    _reserve_input,
    _result_item,
)


class _BindingOrdinalConnection:
    def __init__(self, value: int) -> None:
        self.value = value
        self.sql = ""
        self.parameters: dict[str, object] = {}

    def scalar(self, statement: object, parameters: dict[str, object]) -> int:
        self.sql = str(statement)
        self.parameters = parameters
        return self.value


def test_suite_run_retry_appends_the_next_binding_ordinal() -> None:
    suite_id = uuid.uuid4()
    connection = _BindingOrdinalConnection(2)

    result = _next_suite_run_binding_ordinal(cast(Any, connection), suite_id)

    assert result == 2
    assert "COALESCE(MAX(binding_ordinal), -1) + 1" in connection.sql
    assert connection.parameters == {"suite": suite_id}


class _ConnectionContext:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *_args: object) -> None:
        return None


class _Engine:
    def connect(self) -> _ConnectionContext:
        return _ConnectionContext()


class _Rows:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows

    def one(self) -> dict[str, object]:
        assert len(self._rows) == 1
        return self._rows[0]

    def one_or_none(self) -> dict[str, object] | None:
        assert len(self._rows) <= 1
        return self._rows[0] if self._rows else None


class _EvaluationCohortConnection:
    def __init__(self) -> None:
        self.sql = ""

    def execute(self, statement: object, parameters: dict[str, object]) -> _Rows:
        self.sql = str(statement)
        assert parameters == {"graph": GRAPH_ID, "cohort_version": 11}
        return _Rows(
            [
                {
                    "evaluation_cohort_version_id": uuid.uuid4(),
                    "warmup_start": date(2004, 12, 31),
                    "evaluation_start": date(2007, 1, 3),
                    "evaluation_end": date(2026, 6, 30),
                    "cohort_fingerprint": "a" * 64,
                }
            ]
        )


GRAPH_ID = uuid.uuid4()


def test_default_evaluation_cohort_requires_exact_rankable_frequency_match() -> None:
    connection = _EvaluationCohortConnection()

    result = _default_evaluation_cohort(cast(Any, connection), GRAPH_ID)

    assert result["evaluation_start"] == date(2007, 1, 3)
    assert "cohort.frequency=graph.frequency" in connection.sql
    assert "cohort.research_tier='rankable_research'" in connection.sql
    assert "sp500_free_research_2007_2026_" in connection.sql
    assert "v022_evaluation_cohort_runtime_contract" in connection.sql
    assert "v022_dataset_gate_assessment" in connection.sql
    assert "runtime_artifact.status='published'" in connection.sql
    assert "gate_artifact.status='published'" in connection.sql
    assert "ORDER BY" not in connection.sql


class _SequencedConnection:
    def __init__(self, rows: list[list[dict[str, object]]]) -> None:
        self._rows = rows
        self.queries: list[str] = []

    def execute(self, statement: object, _parameters: dict[str, object]) -> _Rows:
        self.queries.append(str(statement))
        return _Rows(self._rows.pop(0))


def test_existing_suite_binding_replays_without_selecting_new_default() -> None:
    suite_id = uuid.uuid4()
    cohort_id = uuid.uuid4()
    fingerprint = sha256_hexdigest(
        {
            "contract_version": "v0.22.suite_evaluation_cohort_binding.v1",
            "research_suite_id": str(suite_id),
            "evaluation_cohort_version_id": str(cohort_id),
            "frequency": "weekly",
        }
    )
    connection = _SequencedConnection(
        [
            [
                {
                    "evaluation_cohort_version_id": cohort_id,
                    "binding_fingerprint": fingerprint,
                    "frequency": "weekly",
                }
            ]
        ]
    )

    _bind_evaluation_cohort(
        cast(Any, connection),
        research_suite_id=suite_id,
        evaluation_cohort_version_id=cohort_id,
        bound_by="worker",
    )

    assert len(connection.queries) == 1
    assert "v022_research_suite_evaluation_cohort_binding" in connection.queries[0]
    assert "v022_evaluation_cohort_runtime_contract" not in connection.queries[0]


def test_new_suite_binding_fails_closed_without_exact_runtime_and_gate() -> None:
    connection = _SequencedConnection([[], []])

    try:
        _bind_evaluation_cohort(
            cast(Any, connection),
            research_suite_id=uuid.uuid4(),
            evaluation_cohort_version_id=uuid.uuid4(),
            bound_by="worker",
        )
    except ValueError as error:
        assert "exact published runtime and Gate" in str(error)
    else:
        raise AssertionError("Half-published Cohort was admitted")

    admission_sql = connection.queries[1]
    assert "v022_evaluation_cohort_runtime_contract" in admission_sql
    assert "v022_dataset_gate_assessment" in admission_sql
    assert "runtime_artifact.status='published'" in admission_sql
    assert "gate_artifact.status='published'" in admission_sql


class _EvaluationIdentityConnection:
    def __init__(
        self,
        benchmark: dict[str, object],
        reserve: dict[str, object],
    ) -> None:
        self._benchmark = benchmark
        self._reserve = reserve
        self.queries: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement: object, parameters: dict[str, object]) -> _Rows:
        sql = str(statement)
        self.queries.append((sql, parameters))
        if "JOIN catalog.asset spy" in sql:
            return _Rows([self._benchmark])
        if "JOIN data.data_bundle_member market_member" in sql:
            return _Rows([self._reserve])
        raise AssertionError(f"Unexpected evaluation identity query: {sql}")


def test_public_submit_graph_uses_server_owned_runtime_identity(monkeypatch: Any) -> None:
    graph_id = uuid.uuid4()
    submission_key = uuid.uuid4()
    captured: list[object] = []
    expected = SuiteRuntimeSubmission(
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "ready", False
    )
    service = SuiteRuntimeCommandService.__new__(SuiteRuntimeCommandService)
    service._engine = cast(Any, _Engine())

    monkeypatch.setattr(
        "style_rotation.v022.suite_runtime_commands._default_evaluation_cohort",
        lambda _connection, _graph: {
            "evaluation_cohort_version_id": uuid.UUID(int=9),
            "warmup_start": date(2018, 1, 1),
            "evaluation_start": date(2020, 1, 1),
            "evaluation_end": date(2025, 12, 31),
        },
    )

    def submit_command(command: object) -> SuiteRuntimeSubmission:
        captured.append(command)
        return expected

    monkeypatch.setattr(service, "submit_command", submit_command)

    result = service.submit_graph(
        compiled_research_graph_id=graph_id,
        submission_key=submission_key,
        requested_by="researcher",
    )

    command = cast(Any, captured[0])
    assert result == expected
    assert command.compiled_research_graph_id == graph_id
    assert command.submission_key == submission_key
    assert command.requested_range == {
        "start": "2020-01-01",
        "end": "2025-12-31",
    }
    assert command.evaluation_cohort_version_id == uuid.UUID(int=9)
    assert command.materialization_range == {
        "start": "2018-01-01",
        "end": "2025-12-31",
    }
    assert command.executor_version == "v022-first-slice-runtime-37"
    assert command.environment_fingerprint == sha256_hexdigest(
        {
            "contract_version": "v0.22.0",
            "runtime": "v022-first-slice-runtime-37",
        }
    )
    assert _public_status("ready", {"strategy_target": 1}) == "targeting"
    assert _public_status("ready", {"portfolio_cell": 1}) == "evaluating"
    assert _public_status("completed", {}) == "completed"
    assert _public_status(None, {}) == "not_started"


def test_suite_runtime_uses_back_adjusted_product_warning_semantics() -> None:
    assert BACK_ADJUSTED_RESEARCH_SEMANTICS == {
        "price_basis": "back_adjusted",
        "known_at_rule": "xnys_session_close_at_utc",
        "product_warning_required": True,
    }
    assert "evidence_mode" not in BACK_ADJUSTED_RESEARCH_SEMANTICS
    assert "historical_pit_claimed" not in BACK_ADJUSTED_RESEARCH_SEMANTICS


def test_none_branch_resolves_exact_benchmark_and_reserve_bundle_identities() -> None:
    suite_id = uuid.uuid4()
    market_publication_id = uuid.uuid4()
    market_artifact_id = uuid.uuid4()
    calendar_id = uuid.uuid4()
    calendar_artifact_id = uuid.uuid4()
    reserve_publication_id = uuid.uuid4()
    reserve_artifact_id = uuid.uuid4()
    reserve_model_id = uuid.uuid4()
    reserve_model_artifact_id = uuid.uuid4()
    connection = _EvaluationIdentityConnection(
        {
            "dataset_publication_id": market_publication_id,
            "dataset_artifact_id": market_artifact_id,
            "dataset_fingerprint": "1" * 64,
            "calendar_version_id": calendar_id,
            "calendar_artifact_id": calendar_artifact_id,
            "coverage_start": date(2020, 1, 2),
            "coverage_end": date(2025, 12, 31),
        },
        {
            "data_bundle_version_id": uuid.uuid4(),
            "bundle_artifact_id": uuid.uuid4(),
            "dataset_publication_id": reserve_publication_id,
            "dataset_artifact_id": reserve_artifact_id,
            "dataset_fingerprint": "2" * 64,
            "calendar_version_id": calendar_id,
            "calendar_artifact_id": calendar_artifact_id,
            "coverage_start": date(2020, 1, 2),
            "coverage_end": date(2025, 12, 31),
            "reserve_return_model_version_id": reserve_model_id,
            "reserve_return_model_artifact_id": reserve_model_artifact_id,
        },
    )

    benchmark = _benchmark_input(cast(Any, connection), suite_id)
    bundle = _reserve_input(cast(Any, connection), suite_id, benchmark=benchmark)

    assert benchmark.dataset_publication_id == market_publication_id
    assert bundle.reserve.dataset_publication_id == reserve_publication_id
    assert bundle.reserve_return_model_version_id == reserve_model_id
    assert bundle.reserve_return_model_artifact_id == reserve_model_artifact_id
    benchmark_sql, _ = connection.queries[0]
    reserve_sql, reserve_parameters = connection.queries[1]
    assert "input.security_ids" not in benchmark_sql
    assert "bar.asset_id=spy.asset_id" in benchmark_sql
    assert "market_member.dataset_publication_id=" in reserve_sql
    assert "model_dependency.role='reserve_model'" in reserve_sql
    assert reserve_parameters == {
        "suite": suite_id,
        "bundle_key": RESEARCH_DATA_BUNDLE_KEY,
        "benchmark_publication": market_publication_id,
    }


def test_result_item_exposes_frozen_metrics_quality_and_evidence() -> None:
    evidence_id = uuid.uuid4()
    item = _result_item(
        cast(
            Any,
            {
                "outcome": "accepted",
                "quality_status": "passed",
                "metric_document": {
                    "absolute_metrics": [
                        {
                            "metric_key": "cagr",
                            "value": "0.12",
                            "reason_code": None,
                            "observation_count": 252,
                        }
                    ],
                    "relative_metrics": [
                        {
                            "metric_key": "tracking_error",
                            "value": None,
                            "reason_code": "insufficient_observations",
                            "observation_count": 1,
                        }
                    ],
                },
                "result_document": {
                    "execution_identity": {
                        "work_execution_fingerprint": "a" * 64,
                        "evaluation_data_context_fingerprint": "b" * 64,
                    },
                    "evaluation_context": {
                        "benchmark_identity": {
                            "asset_id": uuid.uuid4(),
                            "asset_key": "spy",
                        },
                        "cost_policy_identity": {
                            "policy_key": "linear_bps",
                            "basis_points_per_side": "5",
                        },
                        "execution_delay_sessions": 1,
                        "evaluation_input_cutoff_at": "2025-01-02T21:00:00Z",
                    },
                    "net_path": [{"session_date": "2025-01-02"}],
                    "quality": {
                        "reason_code": None,
                        "details": {"warning_count": 0},
                    },
                },
                "result_evidence_snapshot_id": uuid.uuid4(),
                "result_evidence_artifact_id": evidence_id,
                "evidence_fingerprint": "c" * 64,
                "evidence_class": "locked_historical_test",
                "common_evaluation_panel_id": uuid.uuid4(),
                "common_evaluation_panel_fingerprint": "d" * 64,
                "element_diagnostics": [
                    {
                        "result_element_diagnostic_id": str(uuid.uuid4()),
                        "artifact_id": str(uuid.uuid4()),
                        "diagnostic_fingerprint": "e" * 64,
                        "diagnostic_document": {
                            "feature_variant_key": "return_continuation__w20",
                            "stage_no": 3,
                            "metrics": [
                                {
                                    "metric_key": "mean_rank_ic",
                                    "value": "0.08",
                                    "reason_code": None,
                                }
                            ],
                        },
                    }
                ],
            },
        )
    )

    assert item["diagnostic"]["metrics"] == [
        {
            "metric_group": "absolute",
            "metric_key": "cagr",
            "value": "0.12",
            "value_status": "defined",
            "reason_code": None,
            "observation_count": 252,
        },
        {
            "metric_group": "relative",
            "metric_key": "tracking_error",
            "value": None,
            "value_status": "unavailable",
            "reason_code": "insufficient_observations",
            "observation_count": 1,
        },
    ]
    assert item["diagnostic"]["quality"]["path_session_count"] == 1
    assert item["diagnostic"]["execution"]["benchmark_asset_key"] == "spy"
    assert item["diagnostic"]["evidence"]["result_evidence_artifact_id"] == evidence_id
    assert (
        item["diagnostic"]["elements"][0]["diagnostic_document"]["feature_variant_key"]
        == "return_continuation__w20"
    )
    assert "result_evidence_artifact_id" not in item
