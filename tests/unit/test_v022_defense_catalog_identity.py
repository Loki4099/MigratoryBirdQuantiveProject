from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from style_rotation.v022.catalog import (
    catalog_component_plan,
    diff_catalog_releases,
    lint_catalog_release,
    load_catalog_release,
)
from style_rotation.v022.contracts import DefenseCatalog

PREVIOUS_MANIFEST = Path("v0.22/catalogs/releases/catalog_release.v0.22.4.json")
MANIFEST = Path("v0.22/catalogs/releases/catalog_release.v0.22.5.json")


def _document() -> dict[str, object]:
    return deepcopy(load_catalog_release(MANIFEST).bundle.defense.model_dump(mode="json"))


def test_defense_packages_freeze_independent_timing_and_allocation_identity() -> None:
    loaded = load_catalog_release(MANIFEST)
    defense = loaded.bundle.defense
    packages = {item.variant_key: item for item in defense.defenses}
    timing = {item.variant_key: item for item in defense.timing_policies}
    allocation = defense.allocation_policies[0]

    assert lint_catalog_release(MANIFEST)["component_count"] == 496
    assert set(timing) == {"fixed20_budget", "spy_ma200_tiered_budget"}
    assert packages["fixed20_defense"].version_number == 3
    assert packages["ma200_tiered_defense"].version_number == 2
    assert {item.research_status for item in packages.values()} == {"parity"}
    assert all(
        item.implementation_key.startswith("style_rotation.v022.defense_package.")
        for item in packages.values()
    )
    assert {
        tuple(item.supported_asset_context_keys) for item in packages.values()
    } == {
        (
            "us_style_rotation_4_etf_sample_v1",
            "us_liquid_large_cap_300_pit_v1",
        )
    }
    assert {
        item.timing_policy_ref.variant_key  # type: ignore[union-attr]
        for item in packages.values()
    } == set(timing)
    assert {
        item.defensive_allocation_policy_ref.variant_key  # type: ignore[union-attr]
        for item in packages.values()
    } == {allocation.variant_key}
    assert all(
        item.input_policy.execution_policy == "next_common_session_raw_open"
        for item in timing.values()
    )
    assert timing["fixed20_budget"].input_policy.market_timing_signal_required is False
    assert timing["fixed20_budget"].input_policy.known_at_required is False
    assert (
        timing["spy_ma200_tiered_budget"].input_policy.market_timing_signal_required
        is True
    )
    assert timing["spy_ma200_tiered_budget"].input_policy.known_at_required is True


def test_long_history_basket_is_exact_exploratory_and_has_no_fallback() -> None:
    allocation = load_catalog_release(MANIFEST).bundle.defense.allocation_policies[0]

    assert allocation.asset_registry_catalog_version == "0.21.1"
    assert allocation.asset_set_key == "standard_defensive_basket_long_history_v1"
    assert allocation.research_status == "exploratory"
    assert allocation.formal_eligible is False
    assert allocation.missing_member_policy == "fail"
    assert allocation.reserve_fallback_policy == "forbidden"
    assert allocation.reserve_return_model_ref is not None
    assert allocation.reserve_return_model_ref.model_key == "dgs3mo_cash_accrual_proxy"
    assert allocation.reserve_return_model_ref.version_number == 1
    assert [
        (item.ordinal, item.asset_key, item.component_role, item.sleeve_weight)
        for item in allocation.members
    ] == [
        (0, "synthetic_reserve", "reserve", "0.400000000000000000"),
        (1, "ief", "defensive_asset", "0.250000000000000000"),
        (2, "tlt", "defensive_asset", "0.100000000000000000"),
        (3, "tip", "defensive_asset", "0.150000000000000000"),
        (4, "iau", "defensive_asset", "0.100000000000000000"),
    ]
    assert sum(
        (Decimal(item.sleeve_weight) for item in allocation.members), Decimal()
    ) == Decimal(1)


def test_release_adds_only_nine_explicit_policy_identities() -> None:
    diff = diff_catalog_releases(PREVIOUS_MANIFEST, MANIFEST)
    plan = catalog_component_plan(load_catalog_release(MANIFEST).bundle)

    assert len(plan) == 496
    assert {
        (item.component_kind, item.component_key, item.component_version)
        for item in diff.added
    } == {
        ("defense_timing_family", "fixed_defense_budget", 1),
        ("defense_timing_variant", "fixed20_budget", 1),
        ("defense_timing_version", "fixed20_budget", 1),
        ("defense_timing_family", "market_trend_tiered_budget", 1),
        ("defense_timing_variant", "spy_ma200_tiered_budget", 1),
        ("defense_timing_version", "spy_ma200_tiered_budget", 1),
        ("defense_allocation_family", "standard_fixed_defensive_basket", 1),
        (
            "defense_allocation_variant",
            "standard_defensive_basket_long_history_v1",
            1,
        ),
        (
            "defense_allocation_version",
            "standard_defensive_basket_long_history_v1",
            1,
        ),
        ("defense_version", "fixed20_defense", 3),
        ("defense_version", "ma200_tiered_defense", 2),
    }
    assert {
        (item.component_kind, item.component_key, item.component_version)
        for item in diff.removed
    } == {
        ("defense_version", "fixed20_defense", 2),
        ("defense_version", "ma200_tiered_defense", 1),
    }
    assert not diff.changed


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda document: document["defenses"][0].update({"variant_key": "none"}),
            "null Defense Package",
        ),
        (
            lambda document: document["defenses"][0]["timing_policy_ref"].update(
                {"version_number": 2}
            ),
            "unknown Timing Policy",
        ),
        (
            lambda document: document["defenses"][0].update(
                {"implementation_key": "style_rotation.strategy.v021_topk.fixed20"}
            ),
            "v0.22-owned implementation",
        ),
        (
            lambda document: document["allocation_policies"][0].update(
                {"reserve_return_model_ref": None}
            ),
            "Reserve Return Model",
        ),
        (
            lambda document: document["allocation_policies"][0]["members"][0].update(
                {"sleeve_weight": "0.40000000000000000"}
            ),
            "string_pattern_mismatch",
        ),
        (
            lambda document: document["allocation_policies"][0]["members"][0].update(
                {"sleeve_weight": "0.500000000000000000"}
            ),
            "sum exactly to one",
        ),
    ],
)
def test_composed_catalog_fails_closed(
    mutator: Callable[[dict[str, object]], None], message: str
) -> None:
    document = _document()
    mutator(document)

    with pytest.raises(ValidationError, match=message):
        DefenseCatalog.model_validate(document)


def test_old_release_remains_parseable_and_component_stable() -> None:
    previous = load_catalog_release(PREVIOUS_MANIFEST)

    assert previous.bundle.defense.timing_policies == []
    assert previous.bundle.defense.allocation_policies == []
    assert len(catalog_component_plan(previous.bundle)) == 487
