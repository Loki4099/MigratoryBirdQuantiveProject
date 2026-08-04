from __future__ import annotations

import uuid
from datetime import date

import pytest

from style_rotation.model.diagnostics import (
    ModelEvaluationDataset,
    ModelEvaluationValue,
    calculate_model_diagnostics,
)
from style_rotation.signal.diagnostics import EvaluationReturn


def test_model_diagnostics_add_dispersion_redundancy_and_controlled_ablation() -> None:
    assets = tuple((uuid.uuid4(), key) for key in ("iwf", "iwd", "iwo", "iwn"))
    days = (date(2025, 1, 3), date(2025, 1, 10))
    returns = tuple(
        EvaluationReturn(asset_id, key, day, float(index))
        for day in days
        for index, (asset_id, key) in enumerate(assets)
    )

    def dataset(key: str, dimensions: tuple[str, ...], multiplier: float):
        return ModelEvaluationDataset(
            uuid.uuid4(),
            uuid.uuid4(),
            key,
            "dimension_subset_equal_weight",
            dimensions,
            tuple(
                ModelEvaluationValue(
                    asset_id,
                    asset_key,
                    day,
                    index * multiplier,
                    0 if index == 0 else 1,
                    abs(index * multiplier),
                )
                for day in days
                for index, (asset_id, asset_key) in enumerate(assets)
            ),
        )

    momentum = dataset("momentum", ("momentum_trend",), 1.0)
    risk = dataset("risk", ("volatility_risk",), -1.0)
    combined = dataset("combined", ("momentum_trend", "volatility_risk"), 0.5)
    result = calculate_model_diagnostics(
        (momentum, risk, combined),
        returns,
        frozenset(item[0] for item in assets),
        frequency="weekly",
    )
    assert len(result.periods) == 6
    full = next(
        item
        for item in result.metrics
        if item.model_dataset_id == combined.model_dataset_id and item.window_key == "full"
    )
    assert full.mean_rank_ic == pytest.approx(1.0)
    assert full.mean_score_dispersion > 0
    assert full.mean_confidence == pytest.approx(0.75)
    assert len(result.pairs) == 3
    full_ablations = [item for item in result.ablations if item.window_key == "full"]
    assert {item.removed_dimension_key for item in full_ablations} == {
        "momentum_trend",
        "volatility_risk",
    }
    assert len(result.issues) == 3


def test_model_diagnostics_require_complete_dimension_subset_lattice() -> None:
    assets = tuple((uuid.uuid4(), key) for key in ("iwf", "iwd", "iwo", "iwn"))
    day = date(2025, 1, 3)
    combined = ModelEvaluationDataset(
        uuid.uuid4(),
        uuid.uuid4(),
        "combined",
        "dimension_subset_equal_weight",
        ("momentum_trend", "volatility_risk"),
        tuple(
            ModelEvaluationValue(asset_id, key, day, float(index), 1, float(index))
            for index, (asset_id, key) in enumerate(assets)
        ),
    )
    returns = tuple(
        EvaluationReturn(asset_id, key, day, float(index))
        for index, (asset_id, key) in enumerate(assets)
    )
    with pytest.raises(ValueError, match="incomplete"):
        calculate_model_diagnostics(
            (combined,), returns, frozenset(item[0] for item in assets), frequency="weekly"
        )
