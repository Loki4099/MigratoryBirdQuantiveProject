from __future__ import annotations

import uuid
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy.engine import RowMapping

from style_rotation.v022.ranking_cohort import RankingCohortService


def _row(*, missing: str | None = None) -> RowMapping:
    absolute = [
        {"metric_key": "cagr", "value": "0.12"},
        {"metric_key": "sharpe_ratio", "value": "1.5"},
        {"metric_key": "maximum_drawdown", "value": "-0.20"},
    ]
    relative = [{"metric_key": "cagr_spread", "value": "0.04"}]
    for values in (absolute, relative):
        for item in values:
            if item["metric_key"] == missing:
                item["value"] = None
    return cast(
        RowMapping,
        {
            "result_evidence_snapshot_id": uuid.UUID(int=1),
            "evidence_artifact_id": uuid.UUID(int=2),
            "result_artifact_id": uuid.UUID(int=3),
            "configuration_snapshot_id": uuid.UUID(int=4),
            "quality_document": {
                "metric_document": {
                    "absolute_metrics": absolute,
                    "relative_metrics": relative,
                }
            },
        },
    )


def test_ranking_member_freezes_required_metrics_and_spy_cagr() -> None:
    member = RankingCohortService._member(_row())

    assert member.cagr == Decimal("0.12")
    assert member.cagr_spread == Decimal("0.04")
    assert member.benchmark_cagr == Decimal("0.08")
    assert member.sharpe_ratio == Decimal("1.5")
    assert member.maximum_drawdown == Decimal("-0.20")
    assert len(member.member_fingerprint) == 64


def test_ranking_member_rejects_missing_required_metric() -> None:
    with pytest.raises(ValueError, match="missing metric: sharpe_ratio"):
        RankingCohortService._member(_row(missing="sharpe_ratio"))


def test_ranking_member_preserves_high_precision_spy_cagr() -> None:
    row = cast(
        RowMapping,
        {
            "result_evidence_snapshot_id": uuid.UUID(int=1),
            "evidence_artifact_id": uuid.UUID(int=2),
            "result_artifact_id": uuid.UUID(int=3),
            "configuration_snapshot_id": uuid.UUID(int=4),
            "quality_document": {
                "metric_document": {
                    "absolute_metrics": [
                        {
                            "metric_key": "cagr",
                            "value": "0.1238081023589130076156586769",
                        },
                        {"metric_key": "sharpe_ratio", "value": "1.5"},
                        {"metric_key": "maximum_drawdown", "value": "-0.20"},
                    ],
                    "relative_metrics": [
                        {
                            "metric_key": "cagr_spread",
                            "value": "-0.02186173576821834627166712099",
                        }
                    ],
                }
            },
        },
    )

    member = RankingCohortService._member(row)

    assert member.benchmark_cagr == Decimal(
        "0.14566983812713135388732579789"
    )
