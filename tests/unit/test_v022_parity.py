from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from style_rotation.factor.calculator import FactorPoint
from style_rotation.signal.calculator import SignalPoint
from style_rotation.v022.migration import LegacyOracleOutput
from style_rotation.v022.parity import compare_factor_points, compare_signal_points


def test_factor_comparison_reports_numeric_and_identity_drift() -> None:
    first = uuid.uuid5(uuid.NAMESPACE_URL, "asset:first")
    second = uuid.uuid5(uuid.NAMESPACE_URL, "asset:second")
    actual = (
        FactorPoint(first, "first", date(2026, 1, 2), 1.0 + 5e-13),
        FactorPoint(second, "second", date(2026, 1, 2), 3.0),
    )
    expected = (
        FactorPoint(first, "first", date(2026, 1, 2), 1.0),
        FactorPoint(first, "first", date(2026, 1, 3), 2.0),
    )

    comparison = compare_factor_points(
        actual, expected, _oracle(row_count=2), tolerance=1e-12
    )

    assert comparison.numeric_mismatch_count == 0
    assert comparison.matched_row_count == 1
    assert comparison.missing_row_count == 1
    assert comparison.extra_row_count == 1
    assert not comparison.passed


def test_signal_comparison_keeps_score_state_and_event_failures_separate() -> None:
    asset = uuid.uuid5(uuid.NAMESPACE_URL, "asset:signal")
    actual = (
        SignalPoint(
            asset,
            "signal",
            date(2026, 1, 2),
            Decimal("1.000000000000000002"),
            "positive",
            True,
        ),
    )
    expected = (
        SignalPoint(
            asset,
            "signal",
            date(2026, 1, 2),
            Decimal("1.000000000000000000"),
            "neutral",
            False,
        ),
    )

    comparison = compare_signal_points(
        actual,
        expected,
        _oracle(row_count=1),
        decimal_quantum=Decimal("0.000000000000000001"),
    )

    assert comparison.numeric_mismatch_count == 1
    assert comparison.state_mismatch_count == 1
    assert comparison.event_mismatch_count == 1
    assert not comparison.passed


def _oracle(*, row_count: int) -> LegacyOracleOutput:
    return LegacyOracleOutput(
        artifact_id=uuid.uuid5(uuid.NAMESPACE_URL, "oracle:test"),
        semantic_fingerprint="a" * 64,
        content_hash="b" * 64,
        bundle_key="us_style_daily_research_bundle",
        bundle_version=1,
        universe_key="us_style_rotation_core",
        universe_version=1,
        engine_key="factor_engine",
        engine_version=1,
        coverage_start="2026-01-02",
        coverage_end="2026-01-03",
        row_count=row_count,
    )
