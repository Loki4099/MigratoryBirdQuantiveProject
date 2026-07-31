from datetime import date

from style_rotation.domain.enums import RebalanceFrequency
from style_rotation.signals.calculator import identify_rebalance_pairs


def test_weekly_period_uses_last_observed_session_and_next_session() -> None:
    dates = (
        date(2026, 4, 1),
        date(2026, 4, 2),
        date(2026, 4, 6),
        date(2026, 4, 7),
        date(2026, 4, 8),
        date(2026, 4, 9),
        date(2026, 4, 10),
        date(2026, 4, 13),
    )
    pairs = identify_rebalance_pairs(dates, RebalanceFrequency.WEEKLY, date(2026, 4, 1))
    assert [(item.signal_date, item.execution_date) for item in pairs] == [
        (date(2026, 4, 2), date(2026, 4, 6)),
        (date(2026, 4, 10), date(2026, 4, 13)),
    ]


def test_month_end_requires_a_following_execution_session() -> None:
    dates = (
        date(2026, 1, 29),
        date(2026, 1, 30),
        date(2026, 2, 2),
        date(2026, 2, 27),
    )
    pairs = identify_rebalance_pairs(dates, RebalanceFrequency.MONTHLY, date(2026, 1, 1))
    assert len(pairs) == 1
    assert pairs[0].signal_date == date(2026, 1, 30)
    assert pairs[0].execution_date == date(2026, 2, 2)
