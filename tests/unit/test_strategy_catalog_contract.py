from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from style_rotation.strategy.contracts import StrategyCatalog, expand_strategy_variants

CATALOG_PATH = Path(__file__).parents[2] / "v0.2" / "catalogs" / "strategies.v0.2.0.json"


def _catalog() -> StrategyCatalog:
    return StrategyCatalog.model_validate_json(CATALOG_PATH.read_text(encoding="utf-8"))


def test_catalog_expands_three_semantics_across_canonical_and_sensitivity_k() -> None:
    variants = expand_strategy_variants(_catalog())
    assert len(variants) == 9
    assert {item.k for item in variants} == {1, 2, 3}
    assert sum(item.preset_type == "canonical" for item in variants) == 3
    assert sum(item.preset_type == "sensitivity" for item in variants) == 6
    assert sum(item.auxiliary_signal_key is None for item in variants) == 3
    assert sum(item.auxiliary_signal_key is not None for item in variants) == 6
    assert all(
        item.tie_policy == "proportional_share_of_remaining_slot_budget" for item in variants
    )


def test_catalog_freezes_weekly_monthly_and_next_common_open_execution() -> None:
    catalog = _catalog()
    assert {item.frequency for item in catalog.schedules} == {"weekly", "monthly"}
    assert catalog.execution_policy.delay_common_sessions == 1
    assert catalog.execution_policy.execution_price == "adjusted_open"
    assert catalog.trend_boundary_policy == "zero_is_ineligible"


def test_catalog_rejects_filter_order_that_changes_strategy_meaning() -> None:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    payload["variant_templates"][1]["empty_slot_policy"] = "eligible_backfill_then_reserve"
    with pytest.raises(ValidationError, match="filter order and empty-slot semantics"):
        StrategyCatalog.model_validate(payload)
