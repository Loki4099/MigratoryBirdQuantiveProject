from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

import pandas as pd

from style_rotation.data.providers.fred import FredCsvProvider
from style_rotation.data.providers.yahoo import YahooFinanceProvider
from style_rotation.data.types import MarketPriceRecord, RateObservation


class DataProviderTests(unittest.TestCase):
    @patch("style_rotation.data.providers.yahoo.yf.download")
    def test_yahoo_parameters_and_response_are_normalized(self, download: Mock) -> None:
        columns = pd.MultiIndex.from_product(
            [
                ["IWF"],
                [
                    "Open",
                    "High",
                    "Low",
                    "Close",
                    "Adj Close",
                    "Volume",
                    "Dividends",
                    "Stock Splits",
                ],
            ]
        )
        download.return_value = pd.DataFrame(
            [[100, 102, 99, 101, 50.5, 1000, 0, 0]],
            index=pd.to_datetime(["2026-07-30"]),
            columns=columns,
        )
        batch = YahooFinanceProvider().download(("IWF",), date(2026, 7, 30), date(2026, 7, 31))
        kwargs = download.call_args.kwargs
        self.assertFalse(kwargs["auto_adjust"])
        self.assertFalse(kwargs["repair"])
        self.assertTrue(kwargs["actions"])
        self.assertTrue(kwargs["keepna"])
        record = batch.records[0]
        self.assertIsInstance(record, MarketPriceRecord)
        assert isinstance(record, MarketPriceRecord)
        self.assertEqual(record.adj_close, Decimal("50.5"))

    @patch("style_rotation.data.providers.fred.httpx.get")
    def test_fred_missing_values_are_skipped_and_availability_is_lagged(self, get: Mock) -> None:
        response = Mock()
        response.text = "DATE,DGS3MO\n2026-07-29,.\n2026-07-30,4.25\n"
        get.return_value = response
        batch = FredCsvProvider("https://example.test/fred.csv").download(
            "DGS3MO", date(2026, 7, 29), date(2026, 7, 30)
        )
        response.raise_for_status.assert_called_once()
        self.assertEqual(len(batch.records), 1)
        record = batch.records[0]
        self.assertIsInstance(record, RateObservation)
        assert isinstance(record, RateObservation)
        self.assertEqual(record.available_date, date(2026, 7, 31))
        self.assertEqual(record.annual_rate_percent, Decimal("4.25"))


if __name__ == "__main__":
    unittest.main()
