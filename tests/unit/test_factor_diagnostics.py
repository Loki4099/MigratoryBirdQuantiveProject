from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from style_rotation.factor.diagnostics import (
    DiagnosticDataset,
    DiagnosticValue,
    calculate_factor_diagnostics,
)


def _dataset(
    key: str,
    values: list[float],
    *,
    definition_id: uuid.UUID,
) -> DiagnosticDataset:
    dataset_id = uuid.uuid5(uuid.NAMESPACE_URL, key)
    asset_ids = (uuid.UUID(int=1), uuid.UUID(int=2))
    start = date(2026, 1, 1)
    points = tuple(
        DiagnosticValue(asset_ids[index % 2], start + timedelta(days=index // 2), value)
        for index, value in enumerate(values)
    )
    return DiagnosticDataset(dataset_id, uuid.uuid4(), definition_id, key, points)


def test_factor_diagnostics_summarize_distribution_and_tie_aware_spearman() -> None:
    definition = uuid.uuid4()
    first = _dataset("return__w20", [1, 2, 2, 4, 5, 6], definition_id=definition)
    second = _dataset("return__w60", [6, 5, 5, 3, 2, 1], definition_id=definition)
    third = _dataset("risk__w20", [1, 2, 3, 4, 5, 6], definition_id=uuid.uuid4())

    result = calculate_factor_diagnostics((third, second, first), high_correlation_threshold=0.85)

    assert [item.factor_dataset_id for item in result.summaries] == [
        first.factor_dataset_id,
        second.factor_dataset_id,
        third.factor_dataset_id,
    ]
    first_summary = result.summaries[0]
    assert first_summary.observation_count == 6
    assert first_summary.asset_count == 2
    assert first_summary.median == pytest.approx(3)
    assert first_summary.p05 == pytest.approx(1.25)
    assert first_summary.p95 == pytest.approx(5.75)
    assert len(result.correlations) == 3
    pair = result.correlations[0]
    assert pair.same_definition is True
    assert pair.spearman_correlation == pytest.approx(-1)
    assert pair.high_correlation is True
    assert result.issues == ()


def test_factor_diagnostics_report_constant_series_and_reject_misalignment() -> None:
    definition = uuid.uuid4()
    constant = _dataset("constant", [1, 1, 1, 1], definition_id=definition)
    changing = _dataset("changing", [1, 2, 3, 4], definition_id=uuid.uuid4())
    result = calculate_factor_diagnostics((constant, changing))
    assert result.summaries[1].zero_variance is True
    assert result.correlations[0].spearman_correlation is None
    assert {item.issue_code for item in result.issues} == {
        "zero_variance",
        "undefined_pair_correlation",
    }

    missing = DiagnosticDataset(
        uuid.uuid4(),
        uuid.uuid4(),
        definition,
        "missing",
        changing.values[:-1],
    )
    with pytest.raises(ValueError, match="exact asset-date coverage"):
        calculate_factor_diagnostics((changing, missing))
