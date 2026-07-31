from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.data.cleaning import REQUIRED_SYMBOLS, clean_and_validate
from style_rotation.data.types import MarketPriceRecord, RateObservation


def market_record(symbol: str, trade_date: date, close: str = "100") -> MarketPriceRecord:
    payload = {
        "symbol": symbol,
        "trade_date": trade_date,
        "open_raw": Decimal(close),
        "high_raw": Decimal(close) + 1,
        "low_raw": Decimal(close) - 1,
        "close_raw": Decimal(close),
        "adj_close": Decimal(close) * Decimal("0.5"),
        "volume_raw": 1000,
        "dividends": Decimal(0),
        "stock_splits": Decimal(0),
    }
    close_value = Decimal(close)
    return MarketPriceRecord(
        symbol=symbol,
        trade_date=trade_date,
        open_raw=close_value,
        high_raw=close_value + 1,
        low_raw=close_value - 1,
        close_raw=close_value,
        adj_close=close_value * Decimal("0.5"),
        volume_raw=1000,
        dividends=Decimal(0),
        stock_splits=Decimal(0),
        source_row_hash=sha256_hexdigest(payload),
    )


def rate_record(observation_date: date) -> RateObservation:
    payload = {
        "series_id": "DGS3MO",
        "observation_date": observation_date,
        "available_date": observation_date + timedelta(days=1),
        "annual_rate_percent": Decimal("5"),
    }
    return RateObservation(
        series_id="DGS3MO",
        observation_date=observation_date,
        available_date=observation_date + timedelta(days=1),
        annual_rate_percent=Decimal("5"),
        source_row_hash=sha256_hexdigest(payload),
    )


class DataCleaningTests(unittest.TestCase):
    def test_valid_data_is_adjusted_and_published(self) -> None:
        dates = (date(2026, 7, 29), date(2026, 7, 30))
        market = tuple(market_record(symbol, day) for symbol in REQUIRED_SYMBOLS for day in dates)
        result = clean_and_validate(market, (rate_record(date(2026, 7, 28)),))
        self.assertFalse(result.has_errors)
        self.assertEqual(len(result.prices), 10)
        self.assertEqual(len(result.reserve_returns), 2)
        self.assertEqual(result.prices[0].adj_factor, Decimal("0.5"))
        self.assertEqual(result.prices[0].open_adj, Decimal("50.0"))
        self.assertIsNotNone(result.content_hash)

    def test_missing_symbol_stops_publication(self) -> None:
        day = date(2026, 7, 30)
        market = tuple(market_record(symbol, day) for symbol in REQUIRED_SYMBOLS if symbol != "IWN")
        result = clean_and_validate(market, (rate_record(date(2026, 7, 29)),))
        self.assertTrue(result.has_errors)
        self.assertIn("missing_symbol", {item.rule_code for item in result.issues})
        self.assertIsNone(result.content_hash)

    def test_null_price_stops_publication(self) -> None:
        first_day = date(2026, 7, 29)
        second_day = date(2026, 7, 30)
        market = [
            market_record(symbol, day)
            for symbol in REQUIRED_SYMBOLS
            for day in (first_day, second_day)
        ]
        bad_index = next(
            index
            for index, item in enumerate(market)
            if item.symbol == "IWF" and item.trade_date == second_day
        )
        bad = market[bad_index]
        market[bad_index] = MarketPriceRecord(
            symbol=bad.symbol,
            trade_date=bad.trade_date,
            open_raw=None,
            high_raw=bad.high_raw,
            low_raw=bad.low_raw,
            close_raw=bad.close_raw,
            adj_close=bad.adj_close,
            volume_raw=bad.volume_raw,
            dividends=bad.dividends,
            stock_splits=bad.stock_splits,
            source_row_hash=bad.source_row_hash,
        )
        result = clean_and_validate(tuple(market), (rate_record(date(2026, 7, 28)),))
        self.assertTrue(result.has_errors)
        self.assertIn("null_required_market_value", {item.rule_code for item in result.issues})

    def test_rate_is_never_backfilled_from_the_future(self) -> None:
        day = date(2026, 7, 30)
        market = tuple(market_record(symbol, day) for symbol in REQUIRED_SYMBOLS)
        future_rate = rate_record(day)
        result = clean_and_validate(market, (future_rate,))
        self.assertTrue(result.has_errors)
        self.assertIn("missing_available_reserve_rate", {item.rule_code for item in result.issues})


if __name__ == "__main__":
    unittest.main()
