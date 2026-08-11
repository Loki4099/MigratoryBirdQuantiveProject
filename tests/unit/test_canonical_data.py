from datetime import date
from decimal import Decimal

from style_rotation.data.canonical import (
    SnapshotDocument,
    parse_fred_snapshot,
    parse_market_snapshots,
)


def _market(symbol: str, close: str = "101", adj_close: str = "50.5") -> SnapshotDocument:
    payload = (
        "session_date,Open,High,Low,Close,Adj Close,Volume,Dividends,Stock Splits\n"
        f"2026-07-30,100,102,99,{close},{adj_close},1000,1.25,0\n"
    ).encode()
    return SnapshotDocument(symbol, payload)


def test_market_parser_applies_one_adjustment_factor_to_ohlc() -> None:
    documents = tuple(_market(symbol) for symbol in ("IWF", "IWD", "IWO", "IWN", "SPY"))
    result = parse_market_snapshots(documents, frozenset({date(2026, 7, 30)}))

    assert result.has_errors is False
    assert len(result.bars) == 5
    assert result.bars[0].adjustment_factor == Decimal("0.5")
    assert result.bars[0].open_adj == Decimal("50.0")
    assert result.bars[0].high_adj == Decimal("51.0")
    assert len(result.actions) == 5
    assert result.actions[0].cash_dividend == Decimal("1.25")


def test_market_parser_rejects_missing_required_symbols() -> None:
    result = parse_market_snapshots((_market("IWF"),), frozenset({date(2026, 7, 30)}))
    assert result.has_errors is True
    assert {
        item.subject_key for item in result.issues if item.rule_code == "missing_required_symbol"
    } == {
        "IWD",
        "IWO",
        "IWN",
        "SPY",
    }


def test_fred_parser_preserves_observation_and_conservative_availability() -> None:
    result = parse_fred_snapshot(
        SnapshotDocument(
            "DGS3MO",
            b"observation_date,DGS3MO\n2026-07-29,.\n2026-07-30,4.25\n",
        )
    )
    assert result.has_errors is False
    assert len(result.observations) == 1
    assert result.observations[0].observation_date == date(2026, 7, 30)
    assert result.observations[0].available_date == date(2026, 7, 31)
    assert result.observations[0].annual_rate_percent == Decimal("4.25")
    assert result.issues[0].severity == "info"
