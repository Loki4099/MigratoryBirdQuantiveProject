from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from style_rotation.v022.strategy_product_parity import (
    validate_strategy_product_parity_evidence,
)

REGISTRY = Path("v0.22/m5/strategy-product-migration-registry.v0.22.0.json")
MODEL_PARITY = Path("v0.22/m5/model-parity-evidence.v0.22.0.json")
EVIDENCE = Path("v0.22/m5/strategy-defense-product-parity-evidence.v0.22.0.json")


def test_committed_evidence_proves_strategy_defense_and_product_continuity() -> None:
    registry = _read(REGISTRY)
    model_parity = _read(MODEL_PARITY)
    evidence = _read(EVIDENCE)

    validate_strategy_product_parity_evidence(
        evidence,
        strategy_registry=registry,
        model_parity_evidence=model_parity,
    )

    assert evidence["summary"]["passed"]
    assert evidence["summary"]["active_product_score_point_count"] == 102916
    assert evidence["summary"]["active_product_decision_count"] == 1042
    assert evidence["active_product"]["expected_decisions_fingerprint"] == evidence[
        "active_product"
    ]["actual_decisions_fingerprint"]


def test_evidence_fails_closed_on_product_mismatch_and_content_drift() -> None:
    registry = _read(REGISTRY)
    model_parity = _read(MODEL_PARITY)
    evidence = _read(EVIDENCE)
    mismatch = deepcopy(evidence)
    mismatch["active_product"]["decision_mismatch_count"] = 1
    with pytest.raises(ValueError, match="Product mismatches"):
        validate_strategy_product_parity_evidence(
            mismatch,
            strategy_registry=registry,
            model_parity_evidence=model_parity,
        )

    drift = deepcopy(evidence)
    drift["comparison_policy"]["tolerance"] = "approximate"
    with pytest.raises(ValueError, match="fingerprint drift"):
        validate_strategy_product_parity_evidence(
            drift,
            strategy_registry=registry,
            model_parity_evidence=model_parity,
        )


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value
