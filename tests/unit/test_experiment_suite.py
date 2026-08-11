from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date

import pytest

from style_rotation.experiment.suite_publication import ExperimentCellRequest


def _request(**overrides: object) -> ExperimentCellRequest:
    values: dict[str, object] = {
        "cell_key": "spy_5bps_full",
        "strategy_target_artifact_id": uuid.uuid4(),
        "benchmark_version_artifact_id": uuid.uuid4(),
        "cost_scenario_artifact_id": uuid.uuid4(),
        "metric_catalog_artifact_id": uuid.uuid4(),
        "accounting_engine_artifact_id": uuid.uuid4(),
        "benchmark_engine_artifact_id": uuid.uuid4(),
        "performance_engine_artifact_id": uuid.uuid4(),
        "template_key": "full_history",
        "as_of_date": date(2026, 8, 4),
    }
    values.update(overrides)
    return ExperimentCellRequest(**values)  # type: ignore[arg-type]


def test_cell_key_is_display_identity_not_atomic_semantics() -> None:
    first = _request(cell_key="first")
    second = replace(first, cell_key="second")
    assert first.semantic_payload() == second.semantic_payload()


def test_custom_interval_requires_complete_ordered_dates() -> None:
    with pytest.raises(ValueError, match="requires start and end"):
        _request(template_key="custom")
    with pytest.raises(ValueError, match="end no later"):
        _request(
            template_key="custom",
            custom_start=date(2026, 8, 3),
            custom_end=date(2026, 8, 5),
        )


def test_preset_interval_rejects_custom_dates() -> None:
    with pytest.raises(ValueError, match="cannot include custom dates"):
        _request(custom_start=date(2026, 1, 1))


def test_cell_key_must_be_stable_lowercase_identifier() -> None:
    with pytest.raises(ValueError, match="stable lowercase"):
        _request(cell_key="SPY Full")
