from __future__ import annotations

import uuid
import zlib
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from style_rotation.data.canonical import parse_market_snapshots
from style_rotation.v022.security_market_data import (
    SecurityTerminalEventSpec,
    _security_documents,
    _uniformly_unavailable,
)


def _snapshot(security_id: uuid.UUID, payload: str) -> dict[str, object]:
    return {
        "security_id": security_id,
        "compressed_payload": zlib.compress(payload.encode()),
    }


def test_ticker_segments_merge_under_one_stable_security() -> None:
    security_id = uuid.uuid4()
    header = "session_date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits\n"
    documents = _security_documents(
        (
            _snapshot(
                security_id,
                header + "2020-01-02,10,12,9,11,11,100,0,0\n",
            ),
            _snapshot(
                security_id,
                header + "2020-01-03,11,13,10,12,12,120,0.1,0\n",
            ),
        )  # type: ignore[arg-type]
    )

    result = parse_market_snapshots(
        documents,
        frozenset({date(2020, 1, 2), date(2020, 1, 3)}),
        required_symbols=(str(security_id).upper(),),
    )

    assert not result.has_errors
    assert len(documents) == 1
    assert [item.session_date for item in result.bars] == [date(2020, 1, 2), date(2020, 1, 3)]
    assert result.actions[0].cash_dividend == Decimal("0.1000000000")


def test_overlapping_ticker_segments_fail_on_duplicate_security_session() -> None:
    security_id = uuid.uuid4()
    payload = (
        "session_date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits\n"
        "2020-01-02,10,12,9,11,11,100,0,0\n"
    )
    documents = _security_documents(
        (_snapshot(security_id, payload), _snapshot(security_id, payload))  # type: ignore[arg-type]
    )
    result = parse_market_snapshots(
        documents,
        frozenset({date(2020, 1, 2)}),
        required_symbols=(str(security_id).upper(),),
    )
    assert result.has_errors
    assert any(item.rule_code == "invalid_market_row" for item in result.issues)


def test_reorganization_requires_explicit_settlement_terms() -> None:
    with pytest.raises(ValueError, match="settlement terms"):
        SecurityTerminalEventSpec(
            security_id=uuid.uuid4(),
            event_type="stock_merger",
            effective_session=date(2020, 1, 3),
            known_at=datetime(2020, 1, 2, tzinfo=UTC),
            status="confirmed",
            source_evidence_artifact_id=uuid.uuid4(),
            details={},
        )


def test_security_is_uniformly_unavailable_only_when_every_segment_is_unavailable() -> None:
    uniformly_unavailable = uuid.uuid4()
    partially_available = uuid.uuid4()
    incomplete = uuid.uuid4()

    result = _uniformly_unavailable(
        ({"security_id": partially_available},),  # type: ignore[arg-type]
        (
            {"security_id": uniformly_unavailable},
            {"security_id": partially_available},
            {"security_id": incomplete},
        ),  # type: ignore[arg-type]
        ({"security_id": incomplete},),  # type: ignore[arg-type]
    )

    assert result == frozenset({uniformly_unavailable})
