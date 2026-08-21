from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from style_rotation.factor.calculator import FactorBar
from style_rotation.v022.legacy_compat_runtime import LegacyCompatibilityRuntime

REGISTRY = Path("v0.22/m4/migration-registry.v0.22.3.json")


def test_full_legacy_catalog_executes_deterministically_through_v022_identities() -> None:
    runtime = LegacyCompatibilityRuntime.from_registry_path(REGISTRY)
    bars = _bars_by_asset()
    coverage_end = date(2025, 1, 1) + timedelta(days=279)
    coverage_start = coverage_end - timedelta(days=9)

    first = runtime.execute_all(
        bars,
        candidate_asset_ids=frozenset(bars),
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )
    second = runtime.execute_all(
        bars,
        candidate_asset_ids=frozenset(bars),
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )

    assert first.registry_version == "0.22.3"
    assert len(first.factors) == 28
    assert len(first.signals) == 51
    assert all(len(item.calculation.points) == 40 for item in first.factors)
    assert all(
        len(item.calculation.points) == (
            36 if item.calculation.version.output_type == "crossover_event" else 40
        )
        for item in first.signals
    )
    assert first == second
    assert len(first.runtime_contract_fingerprint) == 64
    assert len(first.input_fingerprint) == 64
    assert len(first.execution_fingerprint) == 64


def test_signal_execution_rejects_a_factor_from_an_unpublished_lineage() -> None:
    runtime = LegacyCompatibilityRuntime.from_registry_path(REGISTRY)
    bars = _bars_by_asset()
    coverage_end = date(2025, 1, 1) + timedelta(days=279)
    coverage_start = coverage_end - timedelta(days=9)
    wrong_factor = runtime.execute_factor(
        "total_return__w120",
        bars,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )

    with pytest.raises(ValueError, match="requires moving_average_ratio__s1_l200"):
        runtime.execute_signal(
            "price_cross_above_ma__moving_average_ratio__s1_l200",
            wrong_factor,
            candidate_asset_ids=frozenset(bars),
        )


def _bars_by_asset() -> dict[uuid.UUID, tuple[FactorBar, ...]]:
    result: dict[uuid.UUID, tuple[FactorBar, ...]] = {}
    start = date(2025, 1, 1)
    for asset_number, asset_key in enumerate(("asset_a", "asset_b", "asset_c", "asset_d"), 1):
        asset_id = uuid.uuid5(uuid.NAMESPACE_URL, f"test:{asset_key}")
        bars = []
        for index in range(280):
            cycle = Decimal(((index + asset_number) % 11 - 5) ** 2) / Decimal(100)
            close = (
                Decimal(50 + asset_number * 7)
                + Decimal(index * asset_number) / Decimal(10)
                + cycle
            )
            bars.append(
                FactorBar(
                    asset_id=asset_id,
                    asset_key=asset_key,
                    session_date=start + timedelta(days=index),
                    close_adj=close,
                    close_raw=close,
                    volume_raw=1_000_000 + asset_number * 10_000 + index * 101,
                    open_raw=close - Decimal("0.1"),
                    high_raw=close + Decimal("0.2"),
                    low_raw=close - Decimal("0.2"),
                    open_adj=close - Decimal("0.1"),
                    high_adj=close + Decimal("0.2"),
                    low_adj=close - Decimal("0.2"),
                )
            )
        result[asset_id] = tuple(bars)
    return result
