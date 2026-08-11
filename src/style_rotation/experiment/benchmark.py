from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from style_rotation.experiment.contracts import TargetAssetWeight, TargetDecision

BenchmarkKey = Literal[
    "spy_buy_and_hold",
    "four_etf_equal_weight_buy_and_hold",
    "four_etf_equal_weight_same_schedule_rebalanced",
]


class BenchmarkTargetError(RuntimeError):
    """Raised when a formal benchmark target cannot be generated."""


def calculate_benchmark_targets(
    *,
    benchmark_key: BenchmarkKey,
    reference_decision_dates: tuple[date, ...],
    candidate_assets: dict[uuid.UUID, str],
    product_benchmark_asset: tuple[uuid.UUID, str],
) -> tuple[TargetDecision, ...]:
    dates = tuple(sorted(set(reference_decision_dates)))
    if not dates or dates != reference_decision_dates:
        raise BenchmarkTargetError("Reference decision dates must be non-empty, unique, and sorted")
    if len(candidate_assets) != 4 or len(set(candidate_assets.values())) != 4:
        raise BenchmarkTargetError("Four-ETF benchmarks require exactly four candidate assets")

    weights: tuple[TargetAssetWeight, ...]
    if benchmark_key == "spy_buy_and_hold":
        asset_id, asset_key = product_benchmark_asset
        weights = (TargetAssetWeight(asset_id, asset_key, Decimal(1)),)
        target_dates = dates[:1]
    elif benchmark_key == "four_etf_equal_weight_buy_and_hold":
        weights = tuple(
            TargetAssetWeight(asset_id, asset_key, Decimal("0.25"))
            for asset_id, asset_key in sorted(candidate_assets.items(), key=lambda item: item[1])
        )
        target_dates = dates[:1]
    elif benchmark_key == "four_etf_equal_weight_same_schedule_rebalanced":
        weights = tuple(
            TargetAssetWeight(asset_id, asset_key, Decimal("0.25"))
            for asset_id, asset_key in sorted(candidate_assets.items(), key=lambda item: item[1])
        )
        target_dates = dates
    else:
        raise ValueError(f"Unsupported benchmark: {benchmark_key}")
    return tuple(TargetDecision(day, weights, Decimal(0)) for day in target_dates)
