from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from style_rotation.data.canonical import CanonicalAction
from style_rotation.v022.market_reconciliation import (
    DEFAULT_RECONSTRUCTION_POLICY,
    V1_RECONSTRUCTION_POLICY,
    V2_RECONSTRUCTION_POLICY,
    GapResolutionEvidenceRef,
    MarketGapResolutionSpec,
    MarketReconciliationSpec,
    _RawBar,
    rebuild_back_adjusted_bars,
)


def test_legacy_v1_raw_actions_rebuild_remains_available_for_exact_replay() -> None:
    security_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    bars = (
        _bar(security_id, asset_id, date(2020, 1, 2), "100", provider_adjusted="999"),
        _bar(security_id, asset_id, date(2020, 1, 3), "50", provider_adjusted="888"),
        _bar(security_id, asset_id, date(2020, 1, 6), "49", provider_adjusted="777"),
    )
    actions = (
        CanonicalAction(str(security_id), date(2020, 1, 3), Decimal(0), Decimal(2)),
        CanonicalAction(str(security_id), date(2020, 1, 6), Decimal(1), Decimal(0)),
    )

    rebuilt = rebuild_back_adjusted_bars(
        bars,
        actions,
        reconstruction_policy=V1_RECONSTRUCTION_POLICY,
    )

    assert [item.close_adj for item in rebuilt] == [
        Decimal("49.0000000000"),
        Decimal("49.0000000000"),
        Decimal("49.0000000000"),
    ]
    assert [item.adjustment_factor for item in rebuilt] == [
        Decimal("0.49000000000000"),
        Decimal("0.98000000000000"),
        Decimal("1.00000000000000"),
    ]


@pytest.mark.parametrize(
    ("prior_close", "current_close", "split_ratio"),
    [
        ("23.0561", "23.4250", "7"),  # AAPL-like provider history
        ("122.3500", "124.7900", "20"),  # AMZN-like provider history
    ],
)
def test_v2_does_not_apply_split_twice_to_split_normalized_ohlcv(
    prior_close: str,
    current_close: str,
    split_ratio: str,
) -> None:
    security_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    bars = (
        _bar(
            security_id,
            asset_id,
            date(2020, 1, 2),
            prior_close,
            provider_adjusted="0.01",
        ),
        _bar(
            security_id,
            asset_id,
            date(2020, 1, 3),
            current_close,
            provider_adjusted="999",
        ),
    )
    actions = (
        CanonicalAction(
            str(security_id),
            date(2020, 1, 3),
            Decimal(0),
            Decimal(split_ratio),
        ),
    )

    rebuilt = rebuild_back_adjusted_bars(bars, actions)

    assert DEFAULT_RECONSTRUCTION_POLICY == V2_RECONSTRUCTION_POLICY
    assert [item.close_adj for item in rebuilt] == [
        Decimal(prior_close).quantize(Decimal("0.0000000001")),
        Decimal(current_close).quantize(Decimal("0.0000000001")),
    ]
    assert [item.adjustment_factor for item in rebuilt] == [
        Decimal("1.00000000000000"),
        Decimal("1.00000000000000"),
    ]


def test_v2_cash_dividend_uses_split_normalized_per_share_basis() -> None:
    security_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    bars = (
        _bar(security_id, asset_id, date(2020, 1, 2), "100", provider_adjusted="1"),
        _bar(security_id, asset_id, date(2020, 1, 3), "98", provider_adjusted="2"),
    )
    actions = (
        CanonicalAction(str(security_id), date(2020, 1, 3), Decimal(2), Decimal(4)),
    )

    rebuilt = rebuild_back_adjusted_bars(bars, actions)

    assert [item.close_adj for item in rebuilt] == [
        Decimal("98.0000000000"),
        Decimal("98.0000000000"),
    ]
    assert [item.adjustment_factor for item in rebuilt] == [
        Decimal("0.98000000000000"),
        Decimal("1.00000000000000"),
    ]


def test_gap_resolution_requires_evidence_and_exact_alternate_usage() -> None:
    base = {
        "primary_dataset_publication_id": uuid.uuid4(),
        "security_id": uuid.uuid4(),
        "gap_key": "aaa_missing_2020_01_03",
        "version_number": 1,
        "gap_type": "missing_bar",
        "gap_start": date(2020, 1, 3),
        "gap_end": date(2020, 1, 3),
        "created_by": "reviewer",
    }
    with pytest.raises(ValueError, match="requires review evidence"):
        MarketGapResolutionSpec(
            **base,
            resolution_kind="retain_primary",
            evidence=(),
        )
    evidence = (GapResolutionEvidenceRef(uuid.uuid4(), "provider_comparison"),)
    with pytest.raises(ValueError, match="Only alternate replacement"):
        MarketGapResolutionSpec(
            **base,
            resolution_kind="replace_with_alternate",
            evidence=evidence,
        )


def test_reconciliation_rejects_duplicate_resolutions() -> None:
    resolution_id = uuid.uuid4()
    with pytest.raises(ValueError, match="must be unique"):
        MarketReconciliationSpec(
            primary_dataset_publication_id=uuid.uuid4(),
            resolution_ids=(resolution_id, resolution_id),
            cleaning_version_id=uuid.uuid4(),
            calendar_version_id=uuid.uuid4(),
            output_dataset_key="sp500_reconciled_v1",
            output_version_number=1,
            created_by="reviewer",
        )


def _bar(
    security_id: uuid.UUID,
    asset_id: uuid.UUID,
    session: date,
    close: str,
    *,
    provider_adjusted: str,
) -> _RawBar:
    value = Decimal(close)
    return _RawBar(
        security_id,
        asset_id,
        session,
        value,
        value,
        value,
        value,
        100,
        Decimal(provider_adjusted),
    )
