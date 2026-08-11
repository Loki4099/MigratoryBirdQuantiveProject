from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import Mock, patch

import httpx
import pandas as pd

from style_rotation.data.providers.snapshots import (
    FredCsvSnapshotAdapter,
    YahooYFinanceSnapshotAdapter,
    snapshot_key,
)


def _clock(*values: datetime) -> Mock:
    return Mock(side_effect=values)


@patch("style_rotation.data.providers.snapshots.yf.download")
def test_yfinance_snapshot_preserves_uncleaned_wrapper_table(download: Mock) -> None:
    columns = pd.MultiIndex.from_product(
        [["IWF"], ["Open", "High", "Low", "Close", "Adj Close", "Volume"]]
    )
    download.return_value = pd.DataFrame(
        [[100.0, 102.0, 99.0, 101.0, 50.5, 1000]],
        index=pd.to_datetime(["2026-07-30"]),
        columns=columns,
    )
    requested = datetime(2026, 8, 3, 1, 0, tzinfo=UTC)
    fetched = datetime(2026, 8, 3, 1, 0, 1, tzinfo=UTC)
    result = YahooYFinanceSnapshotAdapter(clock=_clock(requested, fetched)).fetch(
        "IWF", date(2026, 7, 30), date(2026, 7, 31)
    )

    assert download.call_args.kwargs["auto_adjust"] is False
    assert download.call_args.kwargs["actions"] is True
    assert result.requested_at == requested
    assert result.fetched_at == fetched
    assert result.media_type == "text/csv; charset=utf-8"
    text = result.payload.decode("utf-8")
    assert text.startswith("session_date,Open,High,Low,Close,Adj Close,Volume")
    assert "2026-07-30,100.0,102.0,99.0,101.0,50.5,1000" in text
    assert result.response_metadata["raw_semantics"].startswith("uncleaned wrapper table")


def test_fred_snapshot_preserves_exact_http_response_bytes() -> None:
    payload = b"DATE,DGS3MO\n2026-07-30,4.25\n"
    response = httpx.Response(
        200,
        content=payload,
        headers={"content-type": "text/csv", "etag": '"abc"'},
        request=httpx.Request("GET", "https://example.test/fred.csv?id=DGS3MO"),
    )
    get = Mock(return_value=response)
    requested = datetime(2026, 8, 3, 1, 0, tzinfo=UTC)
    fetched = datetime(2026, 8, 3, 1, 0, 1, tzinfo=UTC)
    result = FredCsvSnapshotAdapter(
        "https://example.test/fred.csv", get=get, clock=_clock(requested, fetched)
    ).fetch("DGS3MO", date(2026, 7, 1), date(2026, 7, 31))

    assert result.payload == payload
    assert result.response_metadata["status_code"] == 200
    assert result.response_metadata["headers"] == {
        "content-type": "text/csv",
        "etag": '"abc"',
    }
    assert get.call_args.kwargs["params"] == {
        "id": "DGS3MO",
        "cosd": "2026-07-01",
        "coed": "2026-07-31",
    }


def test_snapshot_key_requires_timezone_and_is_stable() -> None:
    value = datetime(2026, 8, 3, 1, 2, 3, 456789, tzinfo=UTC)
    assert snapshot_key("IWF", value) == "iwf-20260803T010203456789Z"
