from __future__ import annotations

from datetime import UTC, datetime

import pytest

from style_rotation.v022.operations_probes import _ratio_measurement


def _window() -> tuple[datetime, datetime, datetime]:
    return (
        datetime(2026, 8, 1, tzinfo=UTC),
        datetime(2026, 8, 8, tzinfo=UTC),
        datetime(2026, 8, 8, 1, tzinfo=UTC),
    )


def test_ratio_probe_preserves_observed_counts_and_value() -> None:
    start, end, measured = _window()
    measurement = _ratio_measurement(
        metric_key="queue_terminal_ratio",
        domain_key="queue",
        numerator=9,
        denominator=10,
        window_start_at=start,
        window_end_at=end,
        measured_at=measured,
    )

    assert measurement is not None
    assert str(measurement.observed_value) == "0.9"
    assert measurement.sample_count == 10
    assert measurement.probe_document["numerator"] == 9


def test_empty_probe_window_remains_missing_instead_of_claiming_success() -> None:
    start, end, measured = _window()

    assert (
        _ratio_measurement(
            metric_key="storage_verified_ratio",
            domain_key="storage",
            numerator=0,
            denominator=0,
            window_start_at=start,
            window_end_at=end,
            measured_at=measured,
        )
        is None
    )


def test_ratio_probe_rejects_impossible_counts() -> None:
    start, end, measured = _window()
    with pytest.raises(ValueError, match="ratio invariants"):
        _ratio_measurement(
            metric_key="compile_success_ratio",
            domain_key="compile",
            numerator=2,
            denominator=1,
            window_start_at=start,
            window_end_at=end,
            measured_at=measured,
        )
