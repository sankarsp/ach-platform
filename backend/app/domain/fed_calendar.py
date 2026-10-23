"""Federal Reserve banking-day calendar.

Follows the Federal Reserve holiday policy (Board publication K.8), which is
NOT the OPM federal-employee rule:

* Holiday on Saturday  -> Reserve Banks are OPEN the preceding Friday
  (ACH settles that Friday).
* Holiday on Sunday    -> Reserve Banks are CLOSED the following Monday.

Holidays are computed by rule so the calendar works for any year, and the
computed set can be overridden from data (``extra_closures`` /
``extra_openings``) when the Fed publishes an ad-hoc change. Treasury owns the
calendar as governed data; see ``app.services.calendar_admin``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache


@dataclass(frozen=True)
class Holiday:
    name: str
    date: date            # statutory date
    observed: date | None  # date the Fed is closed, or None if no weekday closure

    @property
    def closes_fed(self) -> bool:
        return self.observed is not None


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th occurrence (1-based) of weekday (Mon=0) in month."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _fed_observed(d: date) -> date | None:
    if d.weekday() == 5:   # Saturday: Reserve Banks open the preceding Friday
        return None
    if d.weekday() == 6:   # Sunday: closed the following Monday
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def fed_holidays(year: int) -> tuple[Holiday, ...]:
    statutory = [
        ("New Year's Day", date(year, 1, 1)),
        ("Martin Luther King Jr. Day", _nth_weekday(year, 1, 0, 3)),
        ("Presidents Day", _nth_weekday(year, 2, 0, 3)),
        ("Memorial Day", _last_weekday(year, 5, 0)),
        ("Juneteenth National Independence Day", date(year, 6, 19)),
        ("Independence Day", date(year, 7, 4)),
        ("Labor Day", _nth_weekday(year, 9, 0, 1)),
        ("Columbus Day", _nth_weekday(year, 10, 0, 2)),
        ("Veterans Day", date(year, 11, 11)),
        ("Thanksgiving Day", _nth_weekday(year, 11, 3, 4)),
        ("Christmas Day", date(year, 12, 25)),
    ]
    return tuple(Holiday(n, d, _fed_observed(d)) for n, d in statutory)


class FedCalendar:
    """Banking-day arithmetic against the Fed calendar."""

    def __init__(self, extra_closures: set[date] | None = None, extra_openings: set[date] | None = None):
        self.extra_closures = set(extra_closures or ())
        self.extra_openings = set(extra_openings or ())

    def closures(self, year: int) -> dict[date, str]:
        out = {h.observed: h.name for h in fed_holidays(year) if h.observed}
        for d in self.extra_closures:
            if d.year == year:
                out[d] = "Ad-hoc closure"
        for d in self.extra_openings:
            out.pop(d, None)
        return out

    def is_banking_day(self, d: date) -> bool:
        if d in self.extra_openings:
            return True
        if d.weekday() >= 5:
            return False
        return d not in self.closures(d.year)

    def holiday_name(self, d: date) -> str | None:
        return self.closures(d.year).get(d)

    def next_banking_day(self, d: date, include_today: bool = False) -> date:
        cur = d if include_today else d + timedelta(days=1)
        while not self.is_banking_day(cur):
            cur += timedelta(days=1)
        return cur

    def previous_banking_day(self, d: date, include_today: bool = False) -> date:
        cur = d if include_today else d - timedelta(days=1)
        while not self.is_banking_day(cur):
            cur -= timedelta(days=1)
        return cur

    def add_banking_days(self, d: date, n: int) -> date:
        cur = d
        step = 1 if n >= 0 else -1
        for _ in range(abs(n)):
            cur = self.next_banking_day(cur) if step > 0 else self.previous_banking_day(cur)
        return cur

    def banking_days_between(self, start: date, end: date) -> int:
        """Banking days after ``start`` up to and including ``end``."""
        if end <= start:
            return 0
        n, cur = 0, start
        while cur < end:
            cur += timedelta(days=1)
            if self.is_banking_day(cur):
                n += 1
        return n

    def non_settlement_days(self, start: date, end: date) -> list[dict]:
        """Weekday Fed closures in [start, end] plus Saturday-holiday Fridays that stay open."""
        rows = []
        # +2: a Saturday New Year's Day affects the prior year's Dec 31
        for year in range(start.year, end.year + 2):
            for h in fed_holidays(year):
                if h.observed and start <= h.observed <= end:
                    rows.append({"date": h.observed, "holiday": h.name, "settles": False,
                                 "note": "Observed Monday (Sunday holiday)" if h.observed != h.date else ""})
                elif h.observed is None:
                    friday = h.date - timedelta(days=1)
                    if start <= friday <= end:
                        rows.append({"date": friday, "holiday": h.name, "settles": True,
                                     "note": "Holiday falls on Saturday; Reserve Banks open"})
        return sorted(rows, key=lambda r: r["date"])


DEFAULT_CALENDAR = FedCalendar()
