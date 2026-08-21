from __future__ import annotations

import uuid
from datetime import date
from typing import Any, cast

import pytest

from style_rotation.api.query import _v022_candidate_dataset_members
from style_rotation.v022 import workspace_context as workspace_context_module
from style_rotation.v022.workspace_context import (
    ActiveV022WorkspaceIdentity,
    GraphWorkspaceContextResolver,
    active_v022_workspace_identity,
)


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def __iter__(self) -> Any:
        return iter(self._rows)


class _Connection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.sql = ""
        self.parameters: dict[str, Any] = {}

    def execute(self, statement: object, parameters: dict[str, Any]) -> _Result:
        self.sql = str(statement)
        self.parameters = parameters
        return _Result(self.rows)


def _identity() -> ActiveV022WorkspaceIdentity:
    return ActiveV022WorkspaceIdentity(
        asset_registry_release_id=uuid.uuid4(),
        asset_registry_artifact_id=uuid.uuid4(),
        asset_registry_version_number=22005,
        asset_registry_catalog_version="0.22.4",
        asset_registry_as_of_date=date(2026, 6, 30),
        universe_history_id=uuid.uuid4(),
        risk_dataset_publication_id=uuid.uuid4(),
        risk_dataset_artifact_id=uuid.uuid4(),
        risk_dataset_key="us_sp500_free_research_frozen_v5_baseline",
        risk_dataset_version_number=1,
        benchmark_dataset_publication_id=uuid.uuid4(),
        benchmark_dataset_artifact_id=uuid.uuid4(),
        benchmark_dataset_key="us_etf_daily_market_frozen_v6_baseline",
        benchmark_dataset_version_number=1,
        dataset_gate_assessment_id=uuid.uuid4(),
        dataset_gate_artifact_id=uuid.uuid4(),
    )


def test_active_identity_requires_exact_registry_gate_and_both_runtimes() -> None:
    identity = _identity()
    row = {
        field: getattr(identity, field)
        for field in identity.__dataclass_fields__
    }
    connection = _Connection([row])

    actual = active_v022_workspace_identity(cast(Any, connection))

    assert actual == identity
    assert connection.parameters == {
        "registry_release_key": "v022_sp500_asset_registry",
        "registry_catalog_version": "0.22.4",
        "risk_dataset_key": "us_sp500_free_research_frozen_v5_baseline",
        "risk_dataset_version": 1,
        "benchmark_dataset_key": "us_etf_daily_market_frozen_v6_baseline",
        "benchmark_dataset_version": 1,
        "dataset_gate_key": "sp500_free_research_v1",
        "dataset_gate_version": 5,
        "cohort_version": 11,
        "weekly_cohort_key": "sp500_free_research_2007_2026_weekly_v11",
        "monthly_cohort_key": "sp500_free_research_2007_2026_monthly_v11",
    }
    assert "weekly_runtime" in connection.sql
    assert "monthly_runtime" in connection.sql
    assert "artifact_dependency" in connection.sql


def test_candidate_members_use_only_exact_active_dataset_ids() -> None:
    identity = _identity()
    security_id = uuid.uuid4()
    connection = _Connection(
        [
            {
                "security_id": security_id,
                "dataset_key": identity.risk_dataset_key,
                "version_number": identity.risk_dataset_version_number,
            }
        ]
    )

    members = _v022_candidate_dataset_members(cast(Any, connection), identity)

    assert members == {security_id: (identity.risk_dataset_key, 1)}
    assert connection.parameters["risk_dataset"] == identity.risk_dataset_publication_id
    assert (
        connection.parameters["benchmark_dataset"]
        == identity.benchmark_dataset_publication_id
    )
    assert "member_count" not in connection.sql
    assert "row_number" not in connection.sql


def test_explicit_selection_rejects_stale_registry_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity()
    monkeypatch.setattr(
        workspace_context_module,
        "require_active_v022_workspace_identity",
        lambda _connection: identity,
    )

    with pytest.raises(LookupError, match="not the active v0.22 Registry"):
        GraphWorkspaceContextResolver().resolve_explicit_selection(
            cast(Any, object()),
            asset_registry_release_id=uuid.uuid4(),
            security_ids=(uuid.uuid4(),),
            data_input_keys=("canonical_market_bars",),
            created_by="researcher",
        )
