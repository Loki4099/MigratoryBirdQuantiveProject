from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from style_rotation.data.forward_return_calculator import ForwardReturnPoint
from style_rotation.v022.aggregation_work_runtime import SignalManifestPoint
from style_rotation.v022.element_diagnostics import calculate_element_diagnostic


def test_element_diagnostic_reports_distribution_coverage_and_rank_ic() -> None:
    assets = tuple((uuid.uuid4(), key) for key in ("a", "b", "c", "d"))
    dates = (date(2025, 1, 3), date(2025, 1, 10))
    scores = ((1, 2, 3, 4), (4, 3, 2, None))
    outcomes = ((0.01, 0.02, 0.03, 0.04), (0.04, 0.03, 0.02, 0.01))
    signals = tuple(
        SignalManifestPoint(
            asset_id,
            asset_key,
            decision_date,
            None if score is None else Decimal(score),
            datetime.combine(decision_date, datetime.min.time(), UTC),
            "frozen-input",
            "warmup" if score is None else None,
        )
        for decision_date, row in zip(dates, scores, strict=True)
        for (asset_id, asset_key), score in zip(assets, row, strict=True)
    )
    returns = tuple(
        ForwardReturnPoint(
            asset_id,
            asset_key,
            decision_date,
            decision_date,
            decision_date,
            Decimal(str(value)),
        )
        for decision_date, row in zip(dates, outcomes, strict=True)
        for (asset_id, asset_key), value in zip(assets, row, strict=True)
    )

    result = calculate_element_diagnostic(
        compiled_feature_occurrence_id=uuid.uuid4(),
        feature_variant_key="return_continuation__w20",
        stage_no=3,
        payload_manifest_id=uuid.uuid4(),
        manifest_artifact_id=uuid.uuid4(),
        manifest_hash="a" * 64,
        research_direction="positive",
        target_key="weekly_next_open_to_next_open",
        target_version_id=uuid.uuid4(),
        target_version_artifact_id=uuid.uuid4(),
        frequency="weekly",
        signal_points=signals,
        forward_returns=returns,
        candidate_asset_ids=frozenset(item[0] for item in assets),
    )

    metrics = {item.metric_key: item for item in result.metrics}
    assert result.expected_observation_count == 8
    assert result.observed_value_count == 7
    assert result.missing_value_count == 1
    assert result.valid_ic_count == 2
    assert metrics["coverage_ratio"].value == "0.875"
    assert metrics["mean_rank_ic"].value == "1"
    assert metrics["positive_ic_ratio"].value == "1"
    assert metrics["value_volatility"].value is not None
    assert metrics["value_skewness"].value is not None
    assert metrics["value_excess_kurtosis"].value is not None
    assert len(result.diagnostic_fingerprint) == 64


def test_element_diagnostic_marks_constant_cross_section_ic_unavailable() -> None:
    assets = tuple((uuid.uuid4(), key) for key in ("a", "b", "c"))
    decision_date = date(2025, 1, 3)
    signals = tuple(
        SignalManifestPoint(
            asset_id,
            asset_key,
            decision_date,
            Decimal("1"),
            datetime(2025, 1, 3, tzinfo=UTC),
            "frozen-input",
            None,
        )
        for asset_id, asset_key in assets
    )
    returns = tuple(
        ForwardReturnPoint(
            asset_id,
            asset_key,
            decision_date,
            decision_date,
            decision_date,
            Decimal(index) / Decimal("100"),
        )
        for index, (asset_id, asset_key) in enumerate(assets, start=1)
    )

    result = calculate_element_diagnostic(
        compiled_feature_occurrence_id=uuid.uuid4(),
        feature_variant_key="constant",
        stage_no=3,
        payload_manifest_id=uuid.uuid4(),
        manifest_artifact_id=uuid.uuid4(),
        manifest_hash="b" * 64,
        research_direction="positive",
        target_key="weekly_next_open_to_next_open",
        target_version_id=uuid.uuid4(),
        target_version_artifact_id=uuid.uuid4(),
        frequency="weekly",
        signal_points=signals,
        forward_returns=returns,
        candidate_asset_ids=frozenset(item[0] for item in assets),
    )

    metrics = {item.metric_key: item for item in result.metrics}
    assert result.valid_ic_count == 0
    assert metrics["mean_rank_ic"].value is None
    assert metrics["mean_rank_ic"].reason_code == "rank_ic_unavailable"
    assert metrics["value_skewness"].reason_code == "insufficient_or_constant_values"


def test_element_diagnostic_applies_negative_research_direction() -> None:
    assets = tuple((uuid.uuid4(), key) for key in ("a", "b", "c"))
    decision_date = date(2025, 1, 3)
    signals = tuple(
        SignalManifestPoint(
            asset_id,
            asset_key,
            decision_date,
            Decimal(score),
            datetime(2025, 1, 3, tzinfo=UTC),
            "frozen-input",
            None,
        )
        for (asset_id, asset_key), score in zip(assets, (3, 2, 1), strict=True)
    )
    returns = tuple(
        ForwardReturnPoint(
            asset_id,
            asset_key,
            decision_date,
            decision_date,
            decision_date,
            Decimal(outcome),
        )
        for (asset_id, asset_key), outcome in zip(
            assets, ("0.01", "0.02", "0.03"), strict=True
        )
    )

    result = calculate_element_diagnostic(
        compiled_feature_occurrence_id=uuid.uuid4(),
        feature_variant_key="low_is_better",
        stage_no=3,
        payload_manifest_id=uuid.uuid4(),
        manifest_artifact_id=uuid.uuid4(),
        manifest_hash="c" * 64,
        research_direction="negative",
        target_key="weekly_next_open_to_next_open",
        target_version_id=uuid.uuid4(),
        target_version_artifact_id=uuid.uuid4(),
        frequency="weekly",
        signal_points=signals,
        forward_returns=returns,
        candidate_asset_ids=frozenset(item[0] for item in assets),
    )

    metrics = {item.metric_key: item for item in result.metrics}
    assert metrics["mean_rank_ic"].value == "1"


def test_unsigned_element_reports_distribution_without_predictive_metrics() -> None:
    assets = tuple((uuid.uuid4(), key) for key in ("a", "b", "c"))
    decision_date = date(2025, 1, 3)
    signals = tuple(
        SignalManifestPoint(
            asset_id,
            asset_key,
            decision_date,
            Decimal(score),
            datetime(2025, 1, 3, tzinfo=UTC),
            "frozen-input",
            None,
        )
        for (asset_id, asset_key), score in zip(assets, (1, 2, 3), strict=True)
    )
    returns = tuple(
        ForwardReturnPoint(
            asset_id,
            asset_key,
            decision_date,
            decision_date,
            decision_date,
            Decimal(outcome),
        )
        for (asset_id, asset_key), outcome in zip(
            assets, ("0.01", "0.02", "0.03"), strict=True
        )
    )

    result = calculate_element_diagnostic(
        compiled_feature_occurrence_id=uuid.uuid4(),
        feature_variant_key="intermediate_value",
        stage_no=1,
        payload_manifest_id=uuid.uuid4(),
        manifest_artifact_id=uuid.uuid4(),
        manifest_hash="d" * 64,
        research_direction="unsigned",
        target_key="weekly_next_open_to_next_open",
        target_version_id=uuid.uuid4(),
        target_version_artifact_id=uuid.uuid4(),
        frequency="weekly",
        signal_points=signals,
        forward_returns=returns,
        candidate_asset_ids=frozenset(item[0] for item in assets),
    )

    metrics = {item.metric_key: item for item in result.metrics}
    assert result.valid_ic_count == 0
    assert metrics["coverage_ratio"].value == "1"
    assert metrics["value_volatility"].value is not None
    assert metrics["mean_rank_ic"].value is None
    assert metrics["mean_rank_ic"].reason_code == "rank_ic_unavailable"


def test_element_diagnostic_uses_the_frozen_daily_selectable_mask() -> None:
    assets = tuple((uuid.uuid4(), key) for key in ("a", "b", "c", "ipo"))
    dates = (date(2025, 1, 3), date(2025, 1, 10))
    signals = tuple(
        SignalManifestPoint(
            asset_id,
            asset_key,
            decision_date,
            None if asset_key == "ipo" and decision_date == dates[0] else Decimal(index),
            datetime.combine(decision_date, datetime.min.time(), UTC),
            "frozen-input",
            "not_yet_selectable" if asset_key == "ipo" and decision_date == dates[0] else None,
        )
        for decision_date in dates
        for index, (asset_id, asset_key) in enumerate(assets, start=1)
    )
    selectable = {
        dates[0]: frozenset(asset_id for asset_id, key in assets if key != "ipo"),
        dates[1]: frozenset(asset_id for asset_id, _ in assets),
    }
    returns = tuple(
        ForwardReturnPoint(
            asset_id,
            asset_key,
            decision_date,
            decision_date,
            decision_date,
            Decimal(index) / Decimal("100"),
        )
        for decision_date in dates
        for index, (asset_id, asset_key) in enumerate(assets, start=1)
        if asset_id in selectable[decision_date]
        and not (asset_key == "ipo" and decision_date == dates[1])
    )

    result = calculate_element_diagnostic(
        compiled_feature_occurrence_id=uuid.uuid4(),
        feature_variant_key="dynamic_membership",
        stage_no=3,
        payload_manifest_id=uuid.uuid4(),
        manifest_artifact_id=uuid.uuid4(),
        manifest_hash="e" * 64,
        research_direction="positive",
        target_key="weekly_next_open_to_next_open",
        target_version_id=uuid.uuid4(),
        target_version_artifact_id=uuid.uuid4(),
        frequency="weekly",
        signal_points=signals,
        forward_returns=returns,
        candidate_asset_ids=frozenset(asset_id for asset_id, _ in assets),
        candidate_asset_ids_by_date=selectable,
        allow_missing_forward_returns=True,
    )

    assert result.expected_observation_count == 7
    assert result.observed_value_count == 7
    assert result.missing_value_count == 0
    assert result.valid_ic_count == 2
    metrics = {item.metric_key: item for item in result.metrics}
    assert metrics["target_coverage_ratio"].value == "0.8571428571428571"
    assert metrics["target_missing_observation_count"].value == "1"
