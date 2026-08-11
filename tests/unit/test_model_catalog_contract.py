from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from style_rotation.model.contracts import ModelCatalog, expand_model_specifications
from style_rotation.signal.contracts import SignalCatalog, generated_signal_key

ROOT = Path(__file__).parents[2] / "v0.2" / "catalogs"


def _catalog() -> ModelCatalog:
    return ModelCatalog.model_validate_json(
        (ROOT / "models.v0.2.0.json").read_text(encoding="utf-8")
    )


def _signal_keys() -> list[str]:
    catalog = SignalCatalog.model_validate_json(
        (ROOT / "signals.v0.2.0.json").read_text(encoding="utf-8")
    )
    return [
        generated_signal_key(template.key, variant)
        for template in catalog.templates
        for variant in template.factor_variants
    ]


def test_model_catalog_expands_all_preregistered_specifications() -> None:
    specifications = expand_model_specifications(_catalog(), _signal_keys())
    assert len(specifications) == 86
    assert sum(item.specification_type == "single_signal" for item in specifications) == 51
    assert (
        sum(item.specification_type == "dimension_subset_equal_weight" for item in specifications)
        == 31
    )
    assert sum(item.specification_type == "fixed_weight" for item in specifications) == 2
    assert sum(item.specification_type == "directional_vote" for item in specifications) == 2
    assert sum(len(item.dimensions) for item in specifications) == 151
    assert (
        sum(len(dimension.components) for item in specifications for dimension in item.dimensions)
        == 331
    )


def test_vote_is_directional_but_reuses_two_level_weight_structure() -> None:
    specifications = {
        item.key: item for item in expand_model_specifications(_catalog(), _signal_keys())
    }
    vote = specifications["five_dimension_majority_vote_v1"]
    assert vote.method == "majority_vote"
    assert vote.tie_output == "neutral"
    assert vote.output_type == "directional_score"
    assert {item.input_transform for item in vote.dimensions} == {"sign"}
    assert [item.weight for item in vote.dimensions] == pytest.approx([0.2] * 5)
    assert all(
        component.input_transform == "identity"
        for dimension in vote.dimensions
        for component in dimension.components
    )


def test_contract_rejects_changed_weight_or_missing_signal_universe() -> None:
    payload = json.loads((ROOT / "models.v0.2.0.json").read_text(encoding="utf-8"))
    payload["representative_dimensions"][0]["weights"][0] = 0
    with pytest.raises(ValidationError, match="weights must all be positive"):
        ModelCatalog.model_validate(payload)
    with pytest.raises(ValueError, match="expects 51 published Signals"):
        expand_model_specifications(_catalog(), _signal_keys()[:-1])
