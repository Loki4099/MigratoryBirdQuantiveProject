from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal, cast

from sqlalchemy import Engine

from style_rotation.ops.v021_execution import (
    V021DatabaseExecutor,
    _complete_accounting_bar_grid,
    _defensive_allocations,
)
from style_rotation.ops.worker import CellExecutionRequest


class _WeeklyPredictiveExecutor(V021DatabaseExecutor):
    def __init__(self) -> None:
        super().__init__(cast(Engine, object()))
        self.asset_ids = (uuid.uuid4(), uuid.uuid4())
        self.bundle_id = uuid.uuid4()
        self.target_artifact_id = uuid.uuid4()
        self.start = date(2025, 1, 1)

    def _predictive_context(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        return {
            "slot_assignments": [{"signal_version_keys": ["momentum"]}],
            "parameters": {"weighting": "equal_by_signal"},
            "normalized_selection": {},
            "data_bundle_version_id": self.bundle_id,
            "data_bundle_artifact_id": uuid.uuid4(),
            "evaluation_target_key": "next_week_open_to_open",
            "frequency": "weekly",
        }

    def _selected_assets(self, selection: dict[str, Any]) -> tuple[uuid.UUID, ...]:
        return self.asset_ids

    def _asset_family(self, asset_ids: tuple[uuid.UUID, ...]) -> Literal["etf", "stock"]:
        return "etf"

    def _signal_points(
        self,
        keys: tuple[str, ...],
        bundle_id: uuid.UUID,
        assets: tuple[uuid.UUID, ...],
        *,
        frequency: str,
    ) -> tuple[
        dict[str, dict[tuple[uuid.UUID, date], tuple[str, Decimal]]],
        dict[str, str],
        list[str],
        dict[str, dict[str, Any]],
    ]:
        daily_points: dict[tuple[uuid.UUID, date], tuple[str, Decimal]] = {}
        for offset in range(182):
            day = self.start + timedelta(days=offset)
            daily_points[(self.asset_ids[0], day)] = ("AAA", Decimal("1"))
            daily_points[(self.asset_ids[1], day)] = ("BBB", Decimal("2"))
        return (
            {"momentum": daily_points},
            {"momentum": "momentum"},
            [str(uuid.uuid4())],
            {
                "momentum": {
                    "signal_version_artifact_id": str(uuid.uuid4()),
                    "signal_dataset_artifact_id": str(uuid.uuid4()),
                    "factor_variant_artifact_id": str(uuid.uuid4()),
                    "version_number": "1",
                    "published_normalization": "none",
                    "published_tie_policy": "average_rank",
                    "direction": "higher_is_better",
                }
            },
        )

    def _forward_return_points(
        self,
        target_key: str,
        bundle_id: uuid.UUID,
        assets: tuple[uuid.UUID, ...],
        *,
        frequency: str,
    ) -> tuple[dict[date, dict[uuid.UUID, Decimal]], uuid.UUID]:
        return (
            {
                self.start + timedelta(days=7 * offset): {
                    self.asset_ids[0]: Decimal("0.01"),
                    self.asset_ids[1]: Decimal("0.02"),
                }
                for offset in range(26)
            },
            self.target_artifact_id,
        )


def test_predictive_target_coverage_uses_frozen_target_periods_not_daily_scores() -> None:
    executor = _WeeklyPredictiveExecutor()
    output = executor.execute_predictive(
        CellExecutionRequest(uuid.uuid4(), uuid.uuid4(), "predictive", {})
    )

    assert output.availability_status == "accepted"
    assert output.metrics["model_point_count"] == 52
    assert output.metrics["common_asset_date_coverage"] == 52
    assert output.metrics["target_period_count"] == 26
    assert output.metrics["aligned_target_period_count"] == 26
    assert output.metrics["target_period_coverage"] == 1.0
    assert output.metrics["nondegenerate_target_ratio"] == 1.0
    assert len(output.series["period_rank_ic"]) == 26
    assert len(output.series["model_scores"]) == 52
    assert output.series["model_input_audit"][0]["common_asset_count"] == 2
    assert "common_asset_ids" not in output.series["model_input_audit"][0]


def test_exploratory_defense_can_fall_back_to_reserve() -> None:
    weights, reserve = _defensive_allocations(
        Decimal("0.2"),
        "standard_defensive_basket_long_history_v1",
        available_assets={"SPY"},
        allow_reserve_fallback=True,
    )

    assert weights == {}
    assert reserve == Decimal("0.2")


def test_only_exploratory_grid_carries_an_unverified_missing_bar() -> None:
    asset_a, asset_b = uuid.uuid4(), uuid.uuid4()
    first = date(2025, 1, 2)
    middle = date(2025, 1, 3)
    last = date(2025, 1, 6)

    def row(asset_id: uuid.UUID, key: str, day: date, price: str) -> dict[str, Any]:
        value = Decimal(price)
        return {
            "asset_id": asset_id,
            "asset_key": key,
            "session_date": day,
            "open_raw": value,
            "open_adj": value,
            "close_raw": value,
            "close_adj": value,
            "volume_raw": Decimal("100"),
        }

    rows = [
        row(asset_a, "AAA", first, "10"),
        row(asset_a, "AAA", last, "12"),
        row(asset_b, "BBB", first, "20"),
        row(asset_b, "BBB", middle, "21"),
        row(asset_b, "BBB", last, "22"),
    ]

    formal = _complete_accounting_bar_grid(rows, [])
    exploratory = _complete_accounting_bar_grid(rows, [], allow_unverified_carry=True)
    assert not any(
        item["asset_id"] == asset_a and item["session_date"] == middle for item in formal
    )
    carried = next(
        item
        for item in exploratory
        if item["asset_id"] == asset_a and item["session_date"] == middle
    )
    assert carried["open_adj"] == carried["close_adj"] == Decimal("10")
    assert carried["volume_raw"] == Decimal("0")
    assert carried["synthetic_reason"] == "exploratory_missing_bar_carry"
