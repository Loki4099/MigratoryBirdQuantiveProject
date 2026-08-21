from __future__ import annotations

import uuid
from datetime import date

import pytest

from style_rotation.v022.processing_calculation_context import (
    ProcessingCalculationContextSpec,
)


def _ordered_ids(prefix: str, count: int) -> tuple[uuid.UUID, ...]:
    return tuple(
        sorted(
            (uuid.uuid5(uuid.NAMESPACE_URL, f"{prefix}:{index}") for index in range(count)),
            key=str,
        )
    )


def _spec(compiled_context_id: uuid.UUID) -> ProcessingCalculationContextSpec:
    feature_ids = _ordered_ids("feature", 2)
    feature_artifacts = _ordered_ids("feature-artifact", 2)
    return ProcessingCalculationContextSpec(
        compiled_execution_data_context_id=compiled_context_id,
        dataset_publication_id=uuid.uuid5(uuid.NAMESPACE_URL, "dataset"),
        dataset_artifact_id=uuid.uuid5(uuid.NAMESPACE_URL, "dataset-artifact"),
        calendar_version_id=uuid.uuid5(uuid.NAMESPACE_URL, "calendar"),
        calendar_artifact_id=uuid.uuid5(uuid.NAMESPACE_URL, "calendar-artifact"),
        coverage_start=date(2004, 12, 31),
        coverage_end=date(2026, 6, 30),
        security_ids=_ordered_ids("security", 3),
        raw_feature_versions=tuple(zip(feature_ids, feature_artifacts, strict=True)),
        source_snapshot_artifact_ids=_ordered_ids("snapshot", 3),
    )


def test_calculation_identity_excludes_graph_and_frequency_axes() -> None:
    weekly = _spec(uuid.uuid5(uuid.NAMESPACE_URL, "weekly-context"))
    monthly = _spec(uuid.uuid5(uuid.NAMESPACE_URL, "monthly-context"))

    assert weekly.context_document == monthly.context_document
    assert weekly.context_fingerprint == monthly.context_fingerprint
    assert "compiled_execution_data_context_id" not in weekly.context_document
    assert "frequency" not in weekly.context_document


def test_calculation_identity_changes_for_any_processing_input() -> None:
    baseline = _spec(uuid.uuid4())
    changed = ProcessingCalculationContextSpec(
        compiled_execution_data_context_id=baseline.compiled_execution_data_context_id,
        dataset_publication_id=uuid.uuid4(),
        dataset_artifact_id=uuid.uuid4(),
        calendar_version_id=baseline.calendar_version_id,
        calendar_artifact_id=baseline.calendar_artifact_id,
        coverage_start=baseline.coverage_start,
        coverage_end=baseline.coverage_end,
        security_ids=baseline.security_ids,
        raw_feature_versions=baseline.raw_feature_versions,
        source_snapshot_artifact_ids=baseline.source_snapshot_artifact_ids,
    )

    assert baseline.context_fingerprint != changed.context_fingerprint


def test_calculation_identity_requires_canonical_order() -> None:
    baseline = _spec(uuid.uuid4())

    with pytest.raises(ValueError, match="security_ids must be sorted and unique"):
        ProcessingCalculationContextSpec(
            compiled_execution_data_context_id=baseline.compiled_execution_data_context_id,
            dataset_publication_id=baseline.dataset_publication_id,
            dataset_artifact_id=baseline.dataset_artifact_id,
            calendar_version_id=baseline.calendar_version_id,
            calendar_artifact_id=baseline.calendar_artifact_id,
            coverage_start=baseline.coverage_start,
            coverage_end=baseline.coverage_end,
            security_ids=tuple(reversed(baseline.security_ids)),
            raw_feature_versions=baseline.raw_feature_versions,
            source_snapshot_artifact_ids=baseline.source_snapshot_artifact_ids,
        )
