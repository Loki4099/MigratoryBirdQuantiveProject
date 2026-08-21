from __future__ import annotations

import uuid
from datetime import date

import pytest

from style_rotation.v022.suite_element_diagnostics import (
    SuiteElementDiagnosticService,
    _asset_members,
    _candidate_mask_from_rows,
    _research_direction,
)


class _Rows:
    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[object]:
        return []


class _Connection:
    sql = ""

    def execute(self, statement: object, _parameters: dict[str, object]) -> _Rows:
        self.sql = str(statement)
        return _Rows()

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Engine:
    def __init__(self) -> None:
        self.connection = _Connection()

    def connect(self) -> _Connection:
        return self.connection


def test_asset_members_preserve_exact_security_identity() -> None:
    first = uuid.uuid4()
    second = uuid.uuid4()

    assert _asset_members(
        {
            "members": [
                {"security_id": str(first), "security_key": "iwf"},
                {"security_id": str(second), "security_key": "iwd"},
            ]
        }
    ) == {first: "iwf", second: "iwd"}


def test_diagnostic_rows_use_the_plan_frozen_data_context_for_simple_catalogs() -> None:
    engine = _Engine()
    service = object.__new__(SuiteElementDiagnosticService)
    service._engine = engine  # type: ignore[attr-defined]

    assert service._rows(uuid.uuid4()) == []

    assert "JOIN experiment.v022_suite_runtime_plan plan" in engine.connection.sql
    assert "plan.compiled_execution_data_context_id" in engine.connection.sql
    assert "v022_configuration_execution_context_binding" not in engine.connection.sql


@pytest.mark.parametrize(
    ("catalog_direction", "expected"),
    (
        ("higher_is_better", "positive"),
        ("higher_is_bullish", "positive"),
        ("lower_is_better", "negative"),
        ("higher_is_bearish", "negative"),
        ("not_applicable", "unsigned"),
    ),
)
def test_research_direction_maps_catalog_semantics(
    catalog_direction: str, expected: str
) -> None:
    assert _research_direction(catalog_direction) == expected


def test_unknown_research_direction_is_rejected() -> None:
    with pytest.raises(ValueError, match="no predictive research direction"):
        _research_direction("unknown")


def test_candidate_mask_preserves_each_cohort_decision_dates_members() -> None:
    first = uuid.uuid4()
    second = uuid.uuid4()
    first_day = date(2025, 1, 3)
    second_day = date(2025, 1, 10)

    assert _candidate_mask_from_rows(
        [
            {"session_date": first_day, "security_id": first},
            {"session_date": second_day, "security_id": first},
            {"session_date": second_day, "security_id": second},
        ],
        frozenset({first, second}),
    ) == {
        first_day: frozenset({first}),
        second_day: frozenset({first, second}),
    }
