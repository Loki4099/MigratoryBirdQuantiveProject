from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import cast

from sqlalchemy.engine import RowMapping

from style_rotation.api.query import (
    _v022_comparison_context,
    _v022_core_metrics,
    _v022_result_series,
)


def test_v022_comparison_context_serializes_decimal_cost() -> None:
    row = cast(
        RowMapping,
        {
            "evaluation_cohort_version_id": uuid.UUID(int=1),
            "evaluation_cohort_fingerprint": "a" * 64,
            "cohort_key": "sp500_weekly_v5",
            "frequency": "weekly",
            "warmup_start": date(2015, 12, 31),
            "evaluation_start": date(2018, 1, 2),
            "evaluation_end": date(2026, 6, 30),
            "benchmark_key": "spy",
            "cost_bps_per_side": Decimal("5.000000"),
            "execution_delay_sessions": 1,
            "price_semantics": "retrospective_yahoo_prices",
        },
    )

    context = _v022_comparison_context(row)

    assert context is not None
    assert context["cost_bps_per_side"] == "5.000000"


def test_v022_core_metrics_use_cagr_spread_for_annualized_excess() -> None:
    metrics = {
        "absolute_metrics": [
            {"metric_key": "cagr", "value": "0.12"},
            {"metric_key": "sharpe_ratio", "value": "1.5"},
            {"metric_key": "maximum_drawdown", "value": "-0.2"},
        ],
        "relative_metrics": [{"metric_key": "cagr_spread", "value": "0.04"}],
    }

    result = _v022_core_metrics(metrics)

    assert result == {
        "cagr": "0.12",
        "benchmark_cagr": "0.08",
        "cagr_spread": "0.04",
        "sharpe_ratio": "1.5",
        "maximum_drawdown": "-0.2",
    }


def test_v022_result_series_aligns_paths_and_retains_endpoints() -> None:
    document = {
        "net_path": [
            {"session_date": "2020-01-02", "normalized_value": "1"},
            {"session_date": "2020-01-03", "normalized_value": "0.9"},
            {"session_date": "2020-01-06", "normalized_value": "1.2"},
        ],
        "benchmark_net_path": [
            {"session_date": "2020-01-02", "normalized_value": "1"},
            {"session_date": "2020-01-03", "normalized_value": "1.0"},
            {"session_date": "2020-01-06", "normalized_value": "1.1"},
        ],
    }

    result = _v022_result_series(document, maximum=2)

    assert result["total_points"] == 3
    assert [item["session_date"].isoformat() for item in result["points"]] == [
        "2020-01-02",
        "2020-01-06",
    ]
    assert result["points"][-1]["excess_nav"] == "1.090909090909090909090909091"
    assert result["points"][-1]["drawdown"] == "0"


def test_v022_result_series_rejects_misaligned_dates() -> None:
    document = {
        "net_path": [{"session_date": "2020-01-02", "normalized_value": "1"}],
        "benchmark_net_path": [
            {"session_date": "2020-01-03", "normalized_value": "1"}
        ],
    }

    try:
        _v022_result_series(document, maximum=2)
    except ValueError as error:
        assert "dates differ" in str(error)
    else:
        raise AssertionError("misaligned v0.22 paths must fail closed")
