from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from style_rotation.factor.contracts import FactorCatalog


def test_frozen_factor_catalog_has_distinct_measurements_and_parameter_instances() -> None:
    path = Path(__file__).parents[2] / "v0.2" / "catalogs" / "factors.v0.2.0.json"
    catalog = FactorCatalog.model_validate_json(path.read_text(encoding="utf-8"))
    assert len(catalog.definitions) == 12
    assert sum(len(item.variants) for item in catalog.definitions) == 28
    assert all(item.definition_version == 1 for item in catalog.definitions)
    assert {item.output_unit for item in catalog.definitions} == {"dimensionless"}
    assert {item.time_semantics for item in catalog.definitions} == {"known_at_session_close"}


def test_factor_catalog_rejects_direction_and_duplicate_parameter_sets() -> None:
    payload = {
        "catalog_type": "factor",
        "catalog_version": "0.2.0",
        "definitions": [
            {
                "key": "measurement",
                "family": "test",
                "formula": "close_adj[t]",
                "inputs": ["close_adj"],
                "implementation_key": "measurement_v1",
                "direction": "higher_is_better",
                "variants": [
                    {
                        "key": "measurement__a",
                        "parameters": {"window": 20},
                        "required_price_observations": 21,
                        "preset_type": "canonical",
                    },
                    {
                        "key": "measurement__b",
                        "parameters": {"window": 20},
                        "required_price_observations": 21,
                        "preset_type": "sensitivity",
                    },
                ],
            }
        ],
    }
    with pytest.raises(ValidationError):
        FactorCatalog.model_validate_json(json.dumps(payload))
