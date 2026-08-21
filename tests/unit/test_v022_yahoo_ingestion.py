from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest

from style_rotation.v022.yahoo_ingestion import (
    YahooIngestionPlanSpec,
    load_yahoo_equity_contract,
)


def test_v022_yahoo_contract_freezes_retrospective_price_semantics() -> None:
    contract = load_yahoo_equity_contract(
        Path("v0.22/catalogs/data_contracts/equity_market.v0.22.0.json")
    )

    assert contract.series_key == "us_equity_daily_market_yahoo"
    assert contract.provider_key == "yahoo_yfinance"
    assert contract.price_semantics == "frozen_retrospective_yahoo_adjusted_price_snapshot"
    assert contract.historical_pit_claimed is False
    assert contract.version.request_template["auto_adjust"] is False
    assert contract.version.request_template["actions"] is True


def test_yahoo_plan_rejects_dynamic_or_empty_identity() -> None:
    with pytest.raises(ValueError, match="start must not follow end"):
        YahooIngestionPlanSpec(
            plan_key="sp500_yahoo_v1",
            version_number=1,
            universe_history_id=uuid.uuid4(),
            data_series_version_id=uuid.uuid4(),
            coverage_start=date(2020, 1, 2),
            coverage_end=date(2019, 1, 2),
            created_by="local",
        )
    with pytest.raises(ValueError, match="key and creator"):
        YahooIngestionPlanSpec(
            plan_key=" ",
            version_number=1,
            universe_history_id=uuid.uuid4(),
            data_series_version_id=uuid.uuid4(),
            coverage_start=date(2019, 1, 2),
            coverage_end=date(2020, 1, 2),
            created_by="local",
        )
