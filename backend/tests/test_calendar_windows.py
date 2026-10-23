from datetime import date, datetime

import pytest

from app.domain.fed_calendar import DEFAULT_CALENDAR as CAL, fed_holidays
from app.domain.windows import ET, funds_availability, plan_settlement, window_instances


def et(*a):
    return datetime(*a, tzinfo=ET)


# ------------------------------------------------------------ Fed calendar
@pytest.mark.parametrize("d", [
    date(2026, 11, 11), date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 5, 31),
    date(2027, 7, 5), date(2027, 9, 6), date(2027, 10, 11), date(2027, 11, 11), date(2027, 11, 25),
])
def test_fed_closures(d):
    assert not CAL.is_banking_day(d)


@pytest.mark.parametrize("d", [date(2027, 6, 18), date(2027, 12, 24), date(2027, 12, 31), date(2026, 7, 3)])
def test_saturday_holiday_friday_stays_open(d):
    # K.8: Reserve Banks open the Friday before a Saturday holiday (unlike the OPM rule)
    assert d.weekday() == 4
    assert CAL.is_banking_day(d)


def test_weekends_closed():
    assert not CAL.is_banking_day(date(2026, 10, 24))
    assert not CAL.is_banking_day(date(2026, 10, 25))


def test_holiday_count():
    assert len(fed_holidays(2027)) == 11
    assert [h.name for h in fed_holidays(2027) if h.observed is None] == [
        "Juneteenth National Independence Day", "Christmas Day"]


def test_banking_day_math_over_long_weekend():
    # Thursday Nov 25 2027 Thanksgiving: 2 banking days after Wed Nov 24 = Mon Nov 29
    assert CAL.add_banking_days(date(2027, 11, 24), 2) == date(2027, 11, 29)
    assert CAL.next_banking_day(date(2027, 7, 2)) == date(2027, 7, 6)


def test_non_settlement_days_includes_open_fridays():
    rows = CAL.non_settlement_days(date(2027, 12, 1), date(2027, 12, 31))
    assert {(r["date"], r["settles"]) for r in rows} == {(date(2027, 12, 24), True), (date(2027, 12, 31), True)}


# ------------------------------------------------------------ windows
def test_same_day_first_window():
    p = plan_settlement(et(2026, 10, 23, 9, 0), date(2026, 10, 23), "CCD", 50_000_00)
    assert p.same_day and p.window.code == "SD1"
    assert p.window.settlement == et(2026, 10, 23, 13, 0)


def test_rolls_to_second_window_after_internal_cutoff():
    p = plan_settlement(et(2026, 10, 23, 9, 50), date(2026, 10, 23), "PPD", 100_00)
    assert p.window.code == "SD2"


def test_missed_last_same_day_rolls_to_next_banking_day():
    # Friday 4:30 p.m. (after 4:00 internal cut-off) -> Friday evening window, settles Monday
    p = plan_settlement(et(2026, 10, 23, 16, 30), date(2026, 10, 23), "PPD", 100_00)
    assert not p.same_day
    assert p.window.code == "ND1"
    assert p.window.settlement_date == date(2026, 10, 26)
    assert any("Missed" in r for r in p.reasons)


def test_iat_never_same_day():
    p = plan_settlement(et(2026, 10, 23, 9, 0), date(2026, 10, 23), "IAT", 100_00)
    assert not p.same_day
    assert any("not eligible" in r for r in p.reasons)


def test_same_day_limit_rises_in_2027():
    over = 2_000_000_00
    assert not plan_settlement(et(2026, 10, 23, 9, 0), date(2026, 10, 23), "CCD", over).same_day
    assert plan_settlement(et(2027, 9, 17, 9, 0), date(2027, 9, 17), "CCD", over).same_day


def test_weekend_effective_date_moves_to_monday():
    p = plan_settlement(et(2026, 10, 24, 11, 0), date(2026, 10, 24), "PPD", 100_00)
    assert p.effective_date == date(2026, 10, 26)
    assert p.window.settlement_date == date(2026, 10, 26)


def test_future_dated_payroll_over_sunday_holiday():
    # Pay date Mon Jul 5 2027 (Fed closed) -> settles Tue Jul 6
    p = plan_settlement(et(2027, 6, 28, 12, 0), date(2027, 7, 5), "PPD", 2_500_00)
    assert p.effective_date == date(2027, 7, 6)
    assert p.window.settlement_date == date(2027, 7, 6)


def test_future_same_day_request_settles_on_effective_date():
    p = plan_settlement(et(2026, 10, 23, 12, 0), date(2026, 10, 27), "CCD", 100_00, same_day_requested=True)
    assert p.same_day and p.window.settlement_date == date(2026, 10, 27)


def test_no_evening_window_on_christmas_friday():
    inst = window_instances(date(2026, 12, 24), 4)
    days = {(w.fed_deadline.date(), w.code) for w in inst}
    assert (date(2026, 12, 25), "ND1") not in days
    assert (date(2026, 12, 26), "ND1") not in days
    assert (date(2026, 12, 27), "ND1") in days  # resumes Sunday evening


def test_funds_availability_9am_from_sept_2026():
    assert funds_availability("ND1", date(2026, 10, 26)).hour == 9
    assert funds_availability("ND1", date(2026, 9, 17)).hour == 17
    assert funds_availability("SD1", date(2026, 10, 26)).strftime("%H:%M") == "13:30"
