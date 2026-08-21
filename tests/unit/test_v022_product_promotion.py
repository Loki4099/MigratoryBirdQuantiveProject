from __future__ import annotations

from datetime import UTC, date, datetime

from style_rotation.v022.product_promotion import _decision_sessions


def _row(day: int) -> tuple[date, datetime]:
    session = date(2026, 8, day)
    return session, datetime(2026, 8, day, 20, tzinfo=UTC)


def test_weekly_product_schedule_uses_last_frozen_session_per_week() -> None:
    rows = (_row(17), _row(18), _row(21), _row(24), _row(28))

    result = _decision_sessions(rows, frequency="weekly")

    assert [item.session_date for item in result] == [date(2026, 8, 21), date(2026, 8, 28)]


def test_monthly_product_schedule_uses_last_frozen_session_per_month() -> None:
    rows = (
        (date(2026, 8, 28), datetime(2026, 8, 28, 20, tzinfo=UTC)),
        (date(2026, 8, 31), datetime(2026, 8, 31, 20, tzinfo=UTC)),
        (date(2026, 9, 1), datetime(2026, 9, 1, 20, tzinfo=UTC)),
        (date(2026, 9, 30), datetime(2026, 9, 30, 20, tzinfo=UTC)),
    )

    result = _decision_sessions(rows, frequency="monthly")

    assert [item.session_date for item in result] == [date(2026, 8, 31), date(2026, 9, 30)]
