from __future__ import annotations

import uuid
from contextlib import nullcontext
from datetime import date
from typing import Any, cast

import pytest
from sqlalchemy import Engine

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.experiment_identity import (
    CommonEvaluationPanelService,
    EvidenceClass,
    PanelObservation,
    _ConfigurationEnsembleBinding,
    _display_document,
    _semantic_document,
)


def _service() -> CommonEvaluationPanelService:
    return CommonEvaluationPanelService(cast(Engine, object()))


def test_common_panel_rejects_empty_mask_before_database_access() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        _service().publish(
            evidence_class="walk_forward_backtest",
            observations=(),
            panel_document={},
        )


def test_common_panel_requires_unique_canonical_order() -> None:
    first = PanelObservation(date(2026, 7, 29), "IWD")
    second = PanelObservation(date(2026, 7, 29), "IWF")
    with pytest.raises(ValueError, match="canonical order"):
        _service().publish(
            evidence_class="walk_forward_backtest",
            observations=(second, first),
            panel_document={},
        )
    with pytest.raises(ValueError, match="must be unique"):
        _service().publish(
            evidence_class="walk_forward_backtest",
            observations=(first, first),
            panel_document={},
        )


def test_common_panel_rejects_unknown_evidence_class() -> None:
    with pytest.raises(ValueError, match="Unsupported Evidence Class"):
        _service().publish(
            evidence_class=cast(EvidenceClass, "historical"),
            observations=(PanelObservation(date(2026, 7, 29), "IWD"),),
            panel_document={},
        )


def test_common_panel_requires_complete_evaluation_cohort_identity() -> None:
    with pytest.raises(ValueError, match="Evaluation Cohort identity must be complete"):
        _service().publish(
            evidence_class="locked_historical_test",
            observations=(PanelObservation(date(2026, 7, 29), "IWD"),),
            panel_document={},
            evaluation_cohort_fingerprint="a" * 64,
        )


class _BatchCaptureConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def execute(self, statement: object, parameters: object) -> None:
        self.calls.append((str(statement), parameters))


def test_common_panel_writes_large_membership_in_canonical_batches() -> None:
    connection = _BatchCaptureConnection()
    panel_id = uuid.uuid4()
    observations = tuple(
        PanelObservation(date(2026, 7, 29), f"asset-{ordinal:05d}")
        for ordinal in range(20_001)
    )

    CommonEvaluationPanelService._write(
        cast(Any, connection),
        uuid.uuid4(),
        panel_id=panel_id,
        fingerprint="f" * 64,
        evidence_class="locked_historical_test",
        observations=observations,
        panel_document={},
        evaluation_cohort_version_id=uuid.uuid4(),
        evaluation_cohort_fingerprint="c" * 64,
    )

    member_calls = [
        parameters
        for statement, parameters in connection.calls
        if "v022_common_evaluation_panel_member" in statement
    ]
    assert [len(cast(list[dict[str, object]], batch)) for batch in member_calls] == [
        10_000,
        10_000,
        1,
    ]
    flattened = [
        row
        for batch in member_calls
        for row in cast(list[dict[str, object]], batch)
    ]
    assert [row["ordinal"] for row in flattened] == list(range(20_001))
    assert all(row["panel"] == panel_id for row in flattened)


class _ExistingPanelResult:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    def mappings(self) -> _ExistingPanelResult:
        return self

    def one_or_none(self) -> dict[str, object]:
        return self._row


class _ExistingPanelConnection:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row
        self.statements: list[str] = []

    def execute(self, statement: object, _parameters: object) -> _ExistingPanelResult:
        self.statements.append(str(statement))
        return _ExistingPanelResult(self.row)


class _ExistingPanelEngine:
    def __init__(self, row: dict[str, object]) -> None:
        self.connection = _ExistingPanelConnection(row)

    def connect(self) -> nullcontext[_ExistingPanelConnection]:
        return nullcontext(self.connection)


def test_common_panel_resolves_cohort_header_without_member_scan() -> None:
    cohort_id = uuid.uuid4()
    fingerprint = "c" * 64
    engine = _ExistingPanelEngine({
        "common_evaluation_panel_id": uuid.uuid4(),
        "artifact_id": uuid.uuid4(),
        "panel_fingerprint": "p" * 64,
        "evidence_class": "locked_historical_test",
        "panel_document": {"frequency": "weekly"},
        "evaluation_cohort_fingerprint": fingerprint,
        "status": "published",
    })

    panel = CommonEvaluationPanelService(
        cast(Engine, engine)
    ).existing_for_evaluation_cohort(
        evaluation_cohort_version_id=cohort_id,
        evaluation_cohort_fingerprint=fingerprint,
    )

    assert panel is not None and panel.reused
    assert panel.observations == ()
    assert len(engine.connection.statements) == 1
    assert "v022_common_evaluation_panel_member" not in engine.connection.statements[0]


def test_configuration_documents_freeze_trainable_ensemble_identity() -> None:
    specification = {
        "contract_version": "v0.22.0",
        "member_policy": "explicit_target_training_cartesian_v1",
        "combination_policy": "equal_within_target_equal_across_targets_v1",
        "target_groups": [],
    }
    ensemble = _ConfigurationEnsembleBinding(
        artifact_id=uuid.uuid4(),
        semantic_document={
            "ensemble_spec_id": str(uuid.uuid4()),
            "artifact_id": str(uuid.uuid4()),
            "ensemble_fingerprint": sha256_hexdigest(specification),
            "specification": specification,
        },
        display_document={
            "member_count": 2,
            "target_group_count": 1,
            "combination_policy": "equal_within_target_equal_across_targets_v1",
            "target_groups": [
                {
                    "target_key": "forward_return_h5",
                    "target_name": "Forward return H5",
                    "members": [
                        {
                            "training_preset_key": "ridge_default_v1",
                            "training_preset_name": "Ridge default",
                        },
                        {
                            "training_preset_key": "ridge_strong_v1",
                            "training_preset_name": "Ridge strong",
                        },
                    ],
                }
            ],
        },
    )
    branch = cast(
        Any,
        {
            "strategy_family_key": "cross_section_rank_top_k",
            "strategy_variant_key": "large_cap",
            "strategy_version_id": uuid.uuid4(),
            "strategy_version_fingerprint": "s" * 64,
            "strategy_parameters": {},
            "schedule_policy": {},
            "execution_policy": {},
            "strategy_parameter_preset_version_id": None,
            "graph_fingerprint": "g" * 64,
            "asset_context_fingerprint": "a" * 64,
            "resolved_data_binding_fingerprint": "d" * 64,
            "frequency": "weekly",
            "aggregation_family_key": "ridge_regression",
            "aggregation_version_id": uuid.uuid4(),
            "aggregation_version_fingerprint": "v" * 64,
            "instance_fingerprint": "i" * 64,
            "execution_mode": "supervised",
            "parameter_preset_version_id": None,
            "target_version_id": None,
            "training_preset_version_id": None,
            "defense_version_id": None,
            "aggregation_name": "Ridge regression",
            "aggregation_version_number": 1,
            "strategy_name": "Cross-section rank Top-K",
            "strategy_version_number": 1,
        },
    )

    semantic = _semantic_document(branch, (), {}, ensemble=ensemble)
    display = _display_document(branch, (), ensemble=ensemble)

    assert semantic["aggregation"]["target_version_id"] is None
    assert semantic["aggregation"]["trainable_ensemble"] == ensemble.semantic_document
    assert display["aggregation"]["trainable_ensemble"] == ensemble.display_document
