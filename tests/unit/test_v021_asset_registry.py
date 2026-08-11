from pathlib import Path

import pytest

from style_rotation.catalog.asset_registry import canonical_registry_payload
from style_rotation.catalog.v021_assets import (
    AssetRegistryCatalog,
    load_asset_registry,
    searchable_document,
)

CATALOG_PATH = Path("v0.21/catalogs/assets.v0.21.1.json")


def test_v021_registry_has_broad_but_honest_asset_coverage() -> None:
    catalog = load_asset_registry(CATALOG_PATH)
    assert len(catalog.categories) == 12
    assert len(catalog.securities) >= 80
    assert {item.key for item in catalog.asset_sets} == {
        "us_style_rotation_4_etf_sample_v1",
        "us_liquid_large_cap_300_pit_v1",
        "standard_defensive_basket_long_history_v1",
        "standard_defensive_basket_tradable_v1",
    }
    pit = next(item for item in catalog.asset_sets if "large_cap_300" in item.key)
    assert not pit.formal_eligible
    assert "P0 gates" in pit.notes


def test_search_document_supports_symbol_name_alias_and_multiple_hits() -> None:
    catalog = load_asset_registry(CATALOG_PATH)
    apple = next(item for item in catalog.securities if item.key == "aapl")
    document = searchable_document(apple)
    assert all(term in document for term in ("aapl", "apple", "appl", "technology"))
    treasury_hits = [
        item.key for item in catalog.securities if "treasury" in searchable_document(item)
    ]
    assert len(treasury_hits) >= 8


def test_publication_payload_sorts_set_backed_tags() -> None:
    payload = canonical_registry_payload(load_asset_registry(CATALOG_PATH))
    for security in payload["securities"]:
        assert security["tags"] == sorted(security["tags"])


def test_reference_index_cannot_claim_strategy_readiness() -> None:
    payload = load_asset_registry(CATALOG_PATH).model_dump(mode="json")
    vix = next(item for item in payload["securities"] if item["key"] == "vix")
    vix["maturity"] = "strategy_ready"
    with pytest.raises(ValueError, match="Reference-only"):
        AssetRegistryCatalog.model_validate(payload)


def test_maturity_gap_must_explain_what_is_missing() -> None:
    payload = load_asset_registry(CATALOG_PATH).model_dump(mode="json")
    qqq = next(item for item in payload["securities"] if item["key"] == "qqq")
    qqq["missing_requirements"] = []
    with pytest.raises(ValueError, match="maturity gap"):
        AssetRegistryCatalog.model_validate(payload)
