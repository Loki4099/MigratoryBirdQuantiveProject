from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any, cast

import pytest

from style_rotation.cli.v022_frozen_sp500_environment import build_parser
from style_rotation.v022.evaluation_cohort import EvaluationCohortPublication
from style_rotation.v022.frozen_sp500_environment import (
    _EVALUATION_END,
    _EVALUATION_START,
    _WARMUP_START,
    FROZEN_SP500_COHORT_VERSION,
    FrozenSp500EnvironmentPublicationService,
    frozen_sp500_cohort_key,
)


def _cohort(seed: int) -> EvaluationCohortPublication:
    return EvaluationCohortPublication(
        uuid.UUID(int=seed),
        uuid.UUID(int=seed + 10),
        f"{seed:064x}",
        5_000,
        1_000,
        2_000,
        False,
    )


def _inputs() -> dict[str, object]:
    return {
        "universe_history_id": uuid.UUID(int=1),
        "risk_dataset_publication_id": uuid.UUID(int=2),
        "benchmark_dataset_publication_id": uuid.UUID(int=3),
        "security_market_quality_report_id": uuid.UUID(int=4),
        "calendar_version_id": uuid.UUID(int=5),
        "dataset_gate_assessment_id": uuid.UUID(int=6),
    }


class _Rows:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _Connection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.sql = ""
        self.parameters: dict[str, object] = {}

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: object, parameters: dict[str, object]) -> _Rows:
        self.sql = str(statement)
        self.parameters = parameters
        return _Rows(self.rows)


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def connect(self) -> _Connection:
        return self.connection


def test_frozen_environment_v10_identity_and_cli_phases() -> None:
    assert FROZEN_SP500_COHORT_VERSION == 11
    assert frozen_sp500_cohort_key("weekly").endswith("weekly_v11")
    assert frozen_sp500_cohort_key("monthly").endswith("monthly_v11")
    with pytest.raises(ValueError):
        frozen_sp500_cohort_key("daily")

    parsed = build_parser().parse_args(["--created-by", "data-governance", "--phase", "cohorts"])
    assert parsed.phase == "cohorts"


def test_cohort_phase_loads_v5_without_requiring_gate4() -> None:
    connection = _Connection(
        [
            {
                **_inputs(),
                "history_status": "published",
                "risk_status": "published",
                "benchmark_status": "published",
                "report_status": "published",
            }
        ]
    )
    service = FrozenSp500EnvironmentPublicationService(cast(Any, _Engine(connection)))

    service._load_exact_cohort_inputs()

    assert connection.parameters["risk_version"] == 1
    assert "gate_version" not in connection.parameters
    assert "v022_dataset_gate_assessment" not in connection.sql
    assert "v022_reconciled_market_dataset_binding" in connection.sql


def test_runtime_phase_loads_exact_gate4() -> None:
    connection = _Connection(
        [
            {
                **_inputs(),
                "risk_status": "published",
                "benchmark_status": "published",
                "report_status": "published",
                "gate_status": "published",
                "ranking_eligibility": "rankable_research",
                "product_eligibility": "eligible_with_warnings",
            }
        ]
    )
    service = FrozenSp500EnvironmentPublicationService(cast(Any, _Engine(connection)))

    service._load_exact_runtime_inputs()

    assert connection.parameters["risk_version"] == 1
    assert connection.parameters["gate_version"] == 5
    assert "v022_dataset_gate_assessment" in connection.sql


def test_cohort_phase_does_not_require_or_publish_gate_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs: list[object] = []

    class _CohortService:
        def __init__(self, _engine: object) -> None:
            pass

        def publish(self, spec: object) -> EvaluationCohortPublication:
            specs.append(spec)
            return _cohort(len(specs))

    class _ForbiddenRuntimeService:
        def __init__(self, _engine: object) -> None:
            raise AssertionError("Cohort phase must not touch runtime/Gate")

    monkeypatch.setattr(
        "style_rotation.v022.frozen_sp500_environment.EvaluationCohortPublicationService",
        _CohortService,
    )
    monkeypatch.setattr(
        "style_rotation.v022.frozen_sp500_environment.CohortRuntimeContractService",
        _ForbiddenRuntimeService,
    )
    service = FrozenSp500EnvironmentPublicationService(cast(Any, object()))
    monkeypatch.setattr(service, "_load_exact_cohort_inputs", _inputs)

    publication = service.publish_cohorts(created_by="data-governance")

    assert [cast(Any, item).frequency for item in specs] == ["weekly", "monthly"]
    assert all(cast(Any, item).version_number == 11 for item in specs)
    assert all(
        cast(Any, item).dataset_publication_id == _inputs()["risk_dataset_publication_id"]
        for item in specs
    )
    assert publication.weekly == _cohort(1)
    assert publication.monthly == _cohort(2)


def test_runtime_phase_requires_exact_loaded_cohorts_and_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    weekly = _cohort(1)
    monthly = _cohort(2)

    class _RuntimeService:
        def __init__(self, _engine: object) -> None:
            pass

        def publish(self, **kwargs: object) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(
                evaluation_cohort_version_id=kwargs["evaluation_cohort_version_id"],
                dataset_gate_assessment_id=kwargs["dataset_gate_assessment_id"],
            )

    monkeypatch.setattr(
        "style_rotation.v022.frozen_sp500_environment.CohortRuntimeContractService",
        _RuntimeService,
    )
    service = FrozenSp500EnvironmentPublicationService(cast(Any, object()))
    monkeypatch.setattr(service, "_load_exact_runtime_inputs", _inputs)
    monkeypatch.setattr(
        service,
        "_load_exact_published_cohorts",
        lambda _frozen: {"weekly": weekly, "monthly": monthly},
    )

    publication = service.publish_runtimes(created_by="data-governance")

    assert [item["evaluation_cohort_version_id"] for item in calls] == [
        weekly.evaluation_cohort_version_id,
        monthly.evaluation_cohort_version_id,
    ]
    assert all(
        item["dataset_gate_assessment_id"] == _inputs()["dataset_gate_assessment_id"]
        for item in calls
    )
    assert publication.dataset_gate_assessment_id == uuid.UUID(int=6)


def test_frozen_dates_remain_exact() -> None:
    assert date(2004, 12, 31) == _WARMUP_START
    assert date(2007, 1, 3) == _EVALUATION_START
    assert date(2026, 6, 30) == _EVALUATION_END
