from __future__ import annotations

from pathlib import Path

from style_rotation.data.contracts import DataContractsCatalog

CATALOG = Path("v0.2/catalogs/data_contracts.v0.2.0.json")


def test_data_contracts_distinguish_tradable_reference_and_calendar_series() -> None:
    catalog = DataContractsCatalog.model_validate_json(CATALOG.read_text(encoding="utf-8"))
    by_key = {item.key: item for item in catalog.series}

    assert by_key["us_etf_daily_market"].subject_type == "asset_listing"
    assert by_key["dgs3mo_daily"].subject_type == "reference_series"
    assert by_key["xnys_calendar"].subject_type == "calendar"
    assert by_key["us_etf_daily_market"].version.request_template["auto_adjust"] is False
    assert "available_at" in by_key["dgs3mo_daily"].version.availability_semantics
