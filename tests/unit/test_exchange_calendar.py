from datetime import date, time

from style_rotation.data.calendar import XNYSCalendarGenerator


def test_xnys_calendar_supports_a_single_session_interval() -> None:
    generated = XNYSCalendarGenerator().generate(date(2026, 7, 30), date(2026, 7, 30))

    assert generated.coverage_start == date(2026, 7, 30)
    assert generated.coverage_end == date(2026, 7, 30)
    assert [session.session_date for session in generated.sessions] == [date(2026, 7, 30)]


def test_xnys_calendar_accepts_a_holiday_interval_boundary() -> None:
    generated = XNYSCalendarGenerator().generate(date(2017, 1, 1), date(2017, 1, 3))

    assert generated.coverage_start == date(2017, 1, 1)
    assert generated.coverage_end == date(2017, 1, 3)
    assert [session.session_date for session in generated.sessions] == [date(2017, 1, 3)]


def test_xnys_calendar_handles_holiday_and_early_close() -> None:
    generated = XNYSCalendarGenerator().generate(date(2026, 11, 25), date(2026, 11, 27))

    assert [item.session_date for item in generated.sessions] == [
        date(2026, 11, 25),
        date(2026, 11, 27),
    ]
    assert generated.sessions[0].is_early_close is False
    assert generated.sessions[1].is_early_close is True
    assert generated.sessions[1].close_at_utc.time() == time(18, 0)
