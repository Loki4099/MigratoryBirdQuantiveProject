from pathlib import Path

import pytest

from style_rotation.workspace.catalog import (
    WorkspaceContractSeed,
    build_component_document,
)

FACTOR_PATH = Path("v0.2/catalogs/factors.v0.2.0.json")
SIGNAL_PATH = Path("v0.2/catalogs/signals.v0.2.0.json")
WORKSPACE_PATH = Path("v0.21/catalogs/workspace_contracts.v0.21.0.json")


def _document() -> dict[str, object]:
    return build_component_document(FACTOR_PATH, SIGNAL_PATH, WORKSPACE_PATH)


def test_component_catalog_keeps_raw_factors_and_legacy_research_families() -> None:
    document = _document()
    factors = document["factor_families"]
    assert isinstance(factors, list)
    raw = [item for item in factors if item["raw"]]
    assert {item["key"] for item in raw} == {
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
        "raw_volume",
    }
    assert any(item["key"] == "total_return" for item in factors)


def test_every_signal_input_exists_and_model_presets_have_valid_slots() -> None:
    document = _document()
    variants = {
        variant["key"] for factor in document["factor_families"] for variant in factor["variants"]
    }
    for signal in document["signal_templates"]:
        assert set(signal["factor_variants"]).issubset(variants)
    families = document["model_families"]
    assert {item["key"] for item in families} == {
        "single_signal",
        "linear_weighted",
        "directional_vote",
        "lightgbm_ranker",
    }
    strategy_families = document["strategy_families"]
    assert {item["key"] for item in strategy_families} == {"multi_etf_top_k", "us_large_cap_top_k"}


def test_planned_training_family_cannot_omit_target() -> None:
    payload = WorkspaceContractSeed.model_validate_json(
        WORKSPACE_PATH.read_text(encoding="utf-8")
    ).model_dump(mode="json")
    family = next(item for item in payload["model_families"] if item["key"] == "lightgbm_ranker")
    family["presets"][0]["target_key"] = None
    with pytest.raises(ValueError, match="explicit target"):
        WorkspaceContractSeed.model_validate(payload)


def test_component_document_canonicalizes_all_set_valued_contract_fields() -> None:
    document = _document()
    linear = next(item for item in document["model_families"] if item["key"] == "linear_weighted")
    frequencies = linear["presets"][0]["supported_frequencies"]
    dimensions = linear["presets"][0]["input_slots"][0]["allowed_dimension_keys"]
    etf = next(item for item in document["strategy_families"] if item["key"] == "multi_etf_top_k")
    assert frequencies == sorted(frequencies)
    assert dimensions == sorted(dimensions)
    assert etf["compatible_model_output_types"] == ["continuous_score"]
