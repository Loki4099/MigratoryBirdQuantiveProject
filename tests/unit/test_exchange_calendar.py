from datetime import date, time

from style_rotation.data.calendar import XNYSCalendarGenerator


def test_xnys_calendar_handles_holiday_and_early_close() -> None:
    generated = XNYSCalendarGenerator().generate(date(2026, 11, 25), date(2026, 11, 27))

    assert [item.session_date for item in generated.sessions] == [
        date(2026, 11, 25),
        date(2026, 11, 27),
    ]
    assert generated.sessions[0].is_early_close is False
    assert generated.sessions[1].is_early_close is True
    assert generated.sessions[1].close_at_utc.time() == time(18, 0)
