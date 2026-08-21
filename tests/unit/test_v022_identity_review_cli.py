from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

from style_rotation.cli import v022_identity_review
from style_rotation.v022.sp500_data_audit import (
    HistoricalIdentityReviewItem,
    Sp500CandidateDates,
)


def test_review_export_produces_stable_case_specs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        v022_identity_review,
        "audit_unmapped_historical_identities",
        lambda **_: (
            HistoricalIdentityReviewItem(
                source_symbol="BRK.B",
                first_observed_session=date(2007, 1, 3),
                last_observed_session=date(2026, 6, 30),
                observed_snapshot_count=42,
                membership_episode_count=1,
            ),
        ),
    )
    manifest_id = uuid.UUID("10000000-0000-4000-8000-000000000001")
    dates = Sp500CandidateDates(
        date(2004, 12, 31), date(2007, 1, 3), date(2026, 6, 30)
    )

    first = v022_identity_review.build_unresolved_export(
        runtime_root=tmp_path,
        external_import_manifest_id=manifest_id,
        provider_scope="fja05680_sp500",
        created_by="local",
        candidate_dates=dates,
    )
    second = v022_identity_review.build_unresolved_export(
        runtime_root=tmp_path,
        external_import_manifest_id=manifest_id,
        provider_scope="fja05680_sp500",
        created_by="local",
        candidate_dates=dates,
    )

    assert first == second
    assert first["contract_version"] == "v0.22.security_identity_review_export.v1"
    assert first["case_count"] == 1
    case = first["cases"][0]
    assert case["case_key"].startswith("sp500_identity_brk_b_")
    assert case["source_symbol"] == "BRK.B"
    assert case["context"] == {"resolution_status": "unresolved"}
