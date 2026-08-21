from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd

from style_rotation.v022.sp500_data_audit import (
    Sp500CandidateDates,
    audit_sp500_seed,
    audit_unmapped_historical_identities,
)


def _write(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def test_seed_audit_reports_identity_and_coverage_gaps_without_writes(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    project = tmp_path / "source"
    data = runtime / "data"
    dataset = "sp500-pit-free-research-2013warmup-2018eval-2026-v3-final-candidate"
    source = (
        b'date,tickers\n2004-12-30,"AAA,OLD"\n'
        b'2007-01-03,"AAA,NEW"\n2026-06-30,"AAA,NEW"\n'
    )
    source_hash = _write(
        data / "external/fja05680/sp500_historical_components_updated.csv", source
    )
    _write(data / "external/fja05680/LICENSE", b"MIT License\nfixture\n")
    _write(data / "external/fja05680/SOURCE_README.md", b"fixture source\n")
    curated = data / "curated" / dataset
    curated.mkdir(parents=True)
    pd.DataFrame(
        {"date": [date(2013, 1, 2), date(2026, 6, 30)], "sid": ["sec::AAA"] * 2}
    ).to_parquet(curated / "prices_daily.parquet", index=False)
    pd.DataFrame(
        {"sid": ["sec::AAA", "sec::OLD"], "provider": ["yfinance", "unavailable"]}
    ).to_parquet(curated / "security_master.parquet", index=False)
    quality = data / "quality" / dataset
    quality.mkdir(parents=True)
    (quality / "security_identity_resolution.csv").write_text(
        "source_sid,canonical_sid\nyf_ticker::AAA,sec::AAA\nyf_ticker::OLD,sec::OLD\n",
        encoding="utf-8",
    )
    manifest = {
        "files": [
            {
                "path": "external/fja05680/sp500_historical_components_updated.csv",
                "sha256": source_hash,
                "size_bytes": len(source),
            }
        ]
    }
    manifest_path = data / "manifests" / f"{dataset}.json"
    manifest_payload = json.dumps(manifest).encode()
    manifest_hash = _write(manifest_path, manifest_payload)
    freeze = {
        "dataset_version": dataset,
        "freeze_status": "frozen_for_free_research",
        "research_tier": "free_research_candidate",
        "formal_eligible": False,
        "manifest": {"sha256": manifest_hash},
    }
    _write(
        project / "metadata/frozen_dataset/FROZEN.json",
        json.dumps(freeze).encode(),
    )

    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    report = audit_sp500_seed(
        runtime_root=runtime,
        source_project_root=project,
        candidate_dates=Sp500CandidateDates(
            date(2004, 12, 31), date(2007, 1, 3), date(2026, 6, 30)
        ),
    )
    after = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))

    assert before == after
    assert report.manifest.matching_sha256_count == 1
    assert report.membership.candidate_initial_snapshot_date == date(2004, 12, 30)
    assert report.membership.unmapped_source_symbols == ("NEW",)
    assert report.market.price_coverage_start == date(2013, 1, 2)
    assert report.market.unavailable_security_count == 1
    assert report.source_membership_importable is True
    assert report.derived_market_seed_directly_rankable is False
    assert report.decision == "blocked_before_publication"
    assert any("historical_security_identity_incomplete" in item for item in report.blockers)
    assert any("candidate_price_history_missing" in item for item in report.blockers)

    review = audit_unmapped_historical_identities(
        runtime_root=runtime,
        candidate_dates=Sp500CandidateDates(
            date(2004, 12, 31), date(2007, 1, 3), date(2026, 6, 30)
        ),
    )
    assert len(review) == 1
    assert review[0].source_symbol == "NEW"
    assert review[0].first_observed_session == date(2007, 1, 3)
    assert review[0].last_observed_session == date(2026, 6, 30)
    assert review[0].observed_snapshot_count == 2
    assert review[0].membership_episode_count == 1
    assert review[0].resolution_status == "unresolved"
