from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.identity_review import (
    SecurityIdentityEvidenceSpec,
    SecurityIdentityResolutionSpec,
    SecurityIdentityReviewCaseSpec,
)


def _case(**overrides: object) -> SecurityIdentityReviewCaseSpec:
    values: dict[str, object] = {
        "external_import_manifest_id": uuid.UUID("10000000-0000-4000-8000-000000000001"),
        "case_key": "sp500.brk_b.2007_2026",
        "version_number": 1,
        "provider_scope": "fja05680_sp500",
        "source_symbol": "BRK.B",
        "first_observed_session": date(2007, 1, 3),
        "last_observed_session": date(2026, 6, 30),
        "observed_snapshot_count": 100,
        "membership_episode_count": 1,
        "reason_code": "historical_security_identity_missing",
        "created_by": "local",
        "context": {"source_rows": [2, 100], "candidate": True},
    }
    values.update(overrides)
    return SecurityIdentityReviewCaseSpec(**values)  # type: ignore[arg-type]


def test_identity_review_case_document_is_canonical_and_exact() -> None:
    first = _case(context={"candidate": True, "source_rows": [2, 100]})
    second = _case(context={"source_rows": [2, 100], "candidate": True})

    assert first.document() == second.document()
    assert sha256_hexdigest(first.document()) == sha256_hexdigest(second.document())
    assert first.document()["source_symbol"] == "BRK.B"
    assert first.document()["first_observed_session"] == "2007-01-03"

    with pytest.raises(ValueError, match="workstation path"):
        _case(context={"source_path": r"C:\\Users\\person\\review.csv"})


def test_identity_evidence_requires_immutable_public_source_identity() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        SecurityIdentityEvidenceSpec(
            review_case_id=uuid.uuid4(),
            evidence_key="brk_b.sec_filing",
            version_number=1,
            evidence_kind="sec_filing",
            source_uri="https://www.sec.gov/Archives/example.txt",
            content_sha256="a" * 64,
            known_at=datetime(2026, 8, 17),
            effective_session=None,
            recorded_by="local",
        )
    with pytest.raises(ValueError, match="approved scheme"):
        SecurityIdentityEvidenceSpec(
            review_case_id=uuid.uuid4(),
            evidence_key="brk_b.local_file",
            version_number=1,
            evidence_kind="manual_analysis",
            source_uri="file:///C:/temp/review.json",
            content_sha256="a" * 64,
            known_at=datetime(2026, 8, 17, tzinfo=UTC),
            effective_session=None,
            recorded_by="local",
        )
    with pytest.raises(ValueError, match="Unsupported Identity Evidence kind"):
        SecurityIdentityEvidenceSpec(
            review_case_id=uuid.uuid4(),
            evidence_key="brk_b.unknown",
            version_number=1,
            evidence_kind="invented",  # type: ignore[arg-type]
            source_uri="https://example.com/evidence",
            content_sha256="a" * 64,
            known_at=datetime(2026, 8, 17, tzinfo=UTC),
            effective_session=None,
            recorded_by="local",
        )


def test_identity_resolution_orders_evidence_without_creating_runtime_repairs() -> None:
    first = uuid.UUID("30000000-0000-4000-8000-000000000002")
    second = uuid.UUID("30000000-0000-4000-8000-000000000001")
    target = uuid.uuid4()
    spec = SecurityIdentityResolutionSpec(
        review_case_id=uuid.uuid4(),
        version_number=1,
        resolution_status="confirmed",
        resolution_kind="ticker_rename",
        evidence_ids=(first, second),
        target_security_id=target,
        resolved_by="reviewer",
        details={"policy": "same_security_declared_identifier_interval"},
    )

    assert spec.canonical_evidence_ids() == (second, first)
    assert spec.document()["evidence_ids"] == [str(second), str(first)]
    assert spec.document()["target_security_id"] == str(target)


def test_identity_resolution_rejects_ambiguous_target_or_history() -> None:
    with pytest.raises(ValueError, match="target Security"):
        SecurityIdentityResolutionSpec(
            review_case_id=uuid.uuid4(),
            version_number=1,
            resolution_status="confirmed",
            resolution_kind="ticker_reuse",
            evidence_ids=(uuid.uuid4(),),
            resolved_by="reviewer",
        )
    with pytest.raises(ValueError, match="must supersede"):
        SecurityIdentityResolutionSpec(
            review_case_id=uuid.uuid4(),
            version_number=2,
            resolution_status="provisional",
            resolution_kind="unavailable",
            evidence_ids=(uuid.uuid4(),),
            resolved_by="reviewer",
        )
