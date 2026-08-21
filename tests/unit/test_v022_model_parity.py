from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from style_rotation.v022.migration import load_migration_registry
from style_rotation.v022.model_compat_runtime import AggregationPoint
from style_rotation.v022.model_migration import load_model_migration_registry
from style_rotation.v022.model_parity import (
    compare_model_points,
    validate_model_parity_evidence,
)

MODEL_REGISTRY = Path("v0.22/m5/model-migration-registry.v0.22.0.json")
SIGNAL_REGISTRY = Path("v0.22/m4/migration-registry.v0.22.3.json")
EVIDENCE = Path("v0.22/m5/model-parity-evidence.v0.22.0.json")


def test_committed_model_evidence_proves_all_172_exact_comparisons() -> None:
    model_registry = load_model_migration_registry(MODEL_REGISTRY)
    signal_registry = load_migration_registry(SIGNAL_REGISTRY)
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    validate_model_parity_evidence(
        evidence,
        model_registry=model_registry,
        signal_registry=signal_registry,
    )

    assert evidence["summary"] == {
        "comparison_count": 172,
        "failed_record_count": 0,
        "model_specification_count": 86,
        "passed": True,
        "passed_record_count": 86,
    }
    assert all(
        comparison["score_mismatch_count"] == 0
        and comparison["direction_mismatch_count"] == 0
        and comparison["confidence_mismatch_count"] == 0
        for record in evidence["records"]
        for comparison in record["comparisons"]
    )


def test_model_evidence_rejects_failed_or_fingerprint_drift() -> None:
    model_registry = load_model_migration_registry(MODEL_REGISTRY)
    signal_registry = load_migration_registry(SIGNAL_REGISTRY)
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    failed = deepcopy(evidence)
    failed["records"][0]["comparisons"][0]["passed"] = False

    with pytest.raises(ValueError, match="failed comparisons"):
        validate_model_parity_evidence(
            failed,
            model_registry=model_registry,
            signal_registry=signal_registry,
        )

    drifted = deepcopy(evidence)
    drifted["comparison_policy"]["score"] = "tolerant"
    with pytest.raises(ValueError, match="fingerprint drift"):
        validate_model_parity_evidence(
            drifted,
            model_registry=model_registry,
            signal_registry=signal_registry,
        )


def test_model_point_comparison_separates_score_direction_and_confidence() -> None:
    asset = uuid.uuid5(uuid.NAMESPACE_URL, "model-parity:asset")
    expected = (
        AggregationPoint(
            asset,
            "asset",
            date(2026, 1, 2),
            Decimal("0.5"),
            "positive",
            Decimal("0.5"),
        ),
    )
    actual = (
        AggregationPoint(
            asset,
            "asset",
            date(2026, 1, 2),
            Decimal("0.4"),
            "neutral",
            Decimal("0.3"),
        ),
    )
    comparison = compare_model_points(
        actual,
        expected,
        {
            "artifact_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "model-parity:oracle")),
            "bundle_key": "bundle",
            "bundle_version": 1,
            "row_count": 1,
        },
    )

    assert comparison.score_mismatch_count == 1
    assert comparison.direction_mismatch_count == 1
    assert comparison.confidence_mismatch_count == 1
    assert comparison.max_abs_score_error == "0.1"
    assert not comparison.passed
