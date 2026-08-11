from datetime import date
from decimal import Decimal

from style_rotation.data.reserve import AvailableRate, calculate_reserve_intervals


def test_reserve_uses_start_available_rate_and_actual_calendar_days() -> None:
    result = calculate_reserve_intervals(
        (AvailableRate(date(2026, 1, 1), date(2026, 1, 2), Decimal("3.65")),),
        (date(2026, 1, 2), date(2026, 1, 5)),
    )

    assert result.has_errors is False
    assert result.intervals[0].calendar_days == 3
    assert result.intervals[0].accrual_factor == Decimal("1.00030000000000")
    assert result.intervals[0].source_available_date == date(2026, 1, 2)


def test_reserve_staleness_warning_and_error_are_distinct() -> None:
    rate = AvailableRate(date(2026, 1, 1), date(2026, 1, 2), Decimal("4"))
    warning = calculate_reserve_intervals((rate,), (date(2026, 1, 9), date(2026, 1, 12)))
    rejected = calculate_reserve_intervals((rate,), (date(2026, 1, 13), date(2026, 1, 14)))

    assert warning.has_errors is False
    assert warning.intervals[0].quality_status == "warning"
    assert warning.issues[0].rule_code == "aging_reserve_rate"
    assert rejected.has_errors is True
    assert rejected.intervals == ()
    assert rejected.issues[0].rule_code == "stale_reserve_rate"
