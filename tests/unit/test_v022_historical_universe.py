from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from style_rotation.v022.historical_universe import (
    HistoricalSp500UniverseSpec,
    MembershipSecurityMapping,
    parse_fja_snapshot_csv,
    resolve_membership_security_mapping,
)


def test_fja_snapshot_parser_freezes_ordered_full_snapshots() -> None:
    snapshots = parse_fja_snapshot_csv(
        'Date,tickers\n2017-12-29,"OLD,AAA,BRK.B"\n'
        '2018-01-03,"AAA,BRK.B,NEW"\n'
    )

    assert tuple(item.effective_session.isoformat() for item in snapshots) == (
        "2017-12-29",
        "2018-01-03",
    )
    assert snapshots[0].source_row_number == 2
    assert snapshots[0].source_symbols == ("AAA", "BRK.B", "OLD")


def test_fja_snapshot_parser_accepts_the_frozen_lowercase_header() -> None:
    snapshots = parse_fja_snapshot_csv('date,tickers\n2018-01-03,"AAA,BRK.B"\n')

    assert tuple(item.effective_session.isoformat() for item in snapshots) == (
        "2018-01-03",
    )
    assert snapshots[0].source_symbols == ("AAA", "BRK.B")


def test_fja_snapshot_parser_rejects_duplicate_or_unordered_source_facts() -> None:
    with pytest.raises(ValueError, match="duplicate symbols"):
        parse_fja_snapshot_csv('Date,tickers\n2018-01-03,"AAA,aaa"\n')
    with pytest.raises(ValueError, match="unique and ordered"):
        parse_fja_snapshot_csv(
            'Date,tickers\n2018-01-03,"AAA"\n2017-12-29,"AAA"\n'
        )


def test_historical_universe_spec_requires_aware_publication_times() -> None:
    snapshots = parse_fja_snapshot_csv('Date,tickers\n2018-01-03,"AAA"\n')
    with pytest.raises(ValueError, match="timezone-aware"):
        HistoricalSp500UniverseSpec(
            external_import_manifest_artifact_id=uuid.uuid4(),
            source_object_logical_key="membership_source",
            universe_key="sp500_history_v1",
            version_number=1,
            methodology_key="sp500_historical_membership",
            methodology_version=1,
            research_tier="exploratory_only",
            snapshots=snapshots,
            mappings=(MembershipSecurityMapping("AAA", uuid.uuid4()),),
            data_cutoff_at=datetime(2026, 8, 16),
            published_at=datetime(2026, 8, 16, tzinfo=UTC),
            created_by="local",
        )


def test_membership_mapping_resolves_declared_ticker_reuse_intervals() -> None:
    original_security = uuid.uuid4()
    reused_security = uuid.uuid4()
    mappings = (
        MembershipSecurityMapping(
            "REUSE",
            original_security,
            valid_to=date(2010, 1, 1),
        ),
        MembershipSecurityMapping(
            "REUSE",
            reused_security,
            valid_from=date(2010, 1, 1),
        ),
    )

    assert resolve_membership_security_mapping(
        mappings,
        source_symbol="reuse",
        effective_session=date(2009, 12, 31),
    ).security_id == original_security
    assert resolve_membership_security_mapping(
        mappings,
        source_symbol="REUSE",
        effective_session=date(2010, 1, 1),
    ).security_id == reused_security


def test_membership_mapping_rejects_unproved_interval_gaps() -> None:
    mappings = (
        MembershipSecurityMapping(
            "GAP",
            uuid.uuid4(),
            valid_to=date(2010, 1, 1),
        ),
        MembershipSecurityMapping(
            "GAP",
            uuid.uuid4(),
            valid_from=date(2010, 1, 2),
        ),
    )

    with pytest.raises(ValueError, match="resolve exactly once"):
        resolve_membership_security_mapping(
            mappings,
            source_symbol="GAP",
            effective_session=date(2010, 1, 1),
        )
