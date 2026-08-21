from __future__ import annotations

from typing import Any, cast

from style_rotation.v022.product_data_disclosure import _warning_codes


def test_product_disclosure_freezes_free_source_warning_summary() -> None:
    row = cast(
        Any,
        {
            "price_semantics": "historical_membership_pit__retrospective_price_snapshot",
            "uniform_exclusion_count": 2,
            "gap_resolution_count": 3,
            "alternate_observation_count": 4,
        },
    )

    assert _warning_codes(row, ("provider_gap_resolved",)) == (
        "free_data_research_product",
        "historical_membership_retrospective",
        "retrospective_price_snapshot",
        "uniform_provider_exclusions_present",
        "manual_gap_resolutions_present",
        "alternate_source_observations_present",
        "provider_gap_resolved",
    )


def test_product_disclosure_deduplicates_warning_codes() -> None:
    row = cast(
        Any,
        {
            "price_semantics": "raw_ohlcv",
            "uniform_exclusion_count": 0,
            "gap_resolution_count": 0,
            "alternate_observation_count": 0,
        },
    )
    assert _warning_codes(row, ("free_data_research_product",)) == (
        "free_data_research_product",
        "historical_membership_retrospective",
    )
