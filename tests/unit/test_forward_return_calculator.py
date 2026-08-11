from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from style_rotation.data.forward_return_calculator import (
    ForwardOpen,
    ForwardReturnCalculationError,
    calculate_forward_returns,
)
from style_rotation.data.forward_return_contracts import ForwardReturnSeed


def _weekly_target() -> ForwardReturnSeed:
    return ForwardReturnSeed(
        key="weekly_next_open_to_next_open",
        version_number=1,
        frequency="weekly",
        decision_rule="last_common_session_of_iso_week",
        decision_time="session_close",
        execution_policy="next_common_session_adjusted_open_v1",
        start_price="open_adj",
        end_price="next_schedule_execution_open_adj",
        execution_lag_sessions=1,
        overlap_policy="non_overlapping_schedule_intervals",
        calendar_key="xnys",
        included_member_roles=["candidate", "benchmark"],
    )


def test_weekly_labels_use_next_schedule_opens_and_exclude_incomplete_tail() -> None:
    sessions = tuple(
        date.fromisoformat(item)
        for item in (
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2024-01-05",
            "2024-01-08",
            "2024-01-09",
            "2024-01-10",
            "2024-01-11",
            "2024-01-12",
            "2024-01-16",
            "2024-01-17",
            "2024-01-18",
            "2024-01-19",
            "2024-01-22",
            "2024-01-23",
            "2024-01-24",
            "2024-01-25",
            "2024-01-26",
        )
    )
    asset_id = uuid.uuid4()
    opens = tuple(
        ForwardOpen(asset_id, "IWF", session, Decimal(index + 100))
        for index, session in enumerate(sessions)
    )
    result = calculate_forward_returns(
        _weekly_target(),
        sessions,
        opens,
        requested_start=date(2024, 1, 1),
        requested_end=date(2024, 1, 22),
    )
    assert [(point.decision_date, point.start_date, point.end_date) for point in result.points] == [
        (date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 16)),
        (date(2024, 1, 12), date(2024, 1, 16), date(2024, 1, 22)),
    ]
    assert result.points[0].forward_return == Decimal("0.048076923076923077")
    assert result.coverage_end == date(2024, 1, 12)


def test_missing_execution_open_fails_instead_of_silently_dropping_asset() -> None:
    sessions = tuple(date(2024, 1, day) for day in range(1, 16) if date(2024, 1, day).weekday() < 5)
    asset_id = uuid.uuid4()
    opens = tuple(
        ForwardOpen(asset_id, "IWF", session, Decimal("100"))
        for session in sessions
        if session != date(2024, 1, 8)
    )
    with pytest.raises(ForwardReturnCalculationError, match="Missing adjusted open"):
        calculate_forward_returns(
            _weekly_target(),
            sessions,
            opens,
            requested_start=date(2024, 1, 1),
            requested_end=date(2024, 1, 15),
        )
