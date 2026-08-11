from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from style_rotation.signal.contracts import SignalCatalog, generated_signal_key

PROJECT_ROOT = Path(__file__).parents[2]
CATALOG_PATH = PROJECT_ROOT / "v0.2" / "catalogs" / "signals.v0.2.0.json"


def _catalog_payload() -> dict[str, object]:
    value = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_signal_catalog_expands_templates_into_explicit_signal_identities() -> None:
    catalog = SignalCatalog.model_validate(_catalog_payload())
    generated = [
        generated_signal_key(template.key, factor_variant_key)
        for template in catalog.templates
        for factor_variant_key in template.factor_variants
    ]
    assert len(catalog.templates) == 27
    assert len(generated) == 51
    assert len(set(generated)) == 51
    assert (
        sum(
            len(template.factor_variants)
            for template in catalog.templates
            if template.product_eligible
        )
        == 41
    )
    assert "return_continuation__total_return__w252" in generated
    assert "price_cross_above_ma__moving_average_ratio__s1_l200" in generated


def test_signal_contract_rejects_implicit_or_malformed_discrete_rules() -> None:
    payload = _catalog_payload()
    templates = payload["templates"]
    assert isinstance(templates, list)
    threshold = next(item for item in templates if item["form"] == "threshold_state")
    threshold.pop("rule")
    with pytest.raises(ValidationError, match="require an explicit rule"):
        SignalCatalog.model_validate(payload)


def test_signal_contract_rejects_duplicate_generated_signal_identity() -> None:
    payload = _catalog_payload()
    templates = payload["templates"]
    assert isinstance(templates, list)
    templates.append(dict(templates[0]))
    with pytest.raises(ValidationError, match="Duplicate signal template key"):
        SignalCatalog.model_validate(payload)
