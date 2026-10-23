"""FedACH processing windows, internal cut-offs and settlement-date planning.

Times follow the FedACH processing schedule (effective Sept 12, 2022). Our
internal client cut-offs sit ``CUTOFF_BUFFER`` before each Fed deadline to
leave room for screening, holds, file build and transmission.

All scheduling is computed in America/New_York and stored in UTC.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .fed_calendar import DEFAULT_CALENDAR, FedCalendar
from .rules import NEXT_DAY_CREDIT_AVAILABILITY, SAME_DAY_LIMIT_CENTS, SEC_CODES

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
CUTOFF_BUFFER = timedelta(minutes=45)


@dataclass(frozen=True)
class WindowDef:
    code: str
    label: str
    same_day: bool
    fed_deadline: time
    distribution: time
    settlement: time
    deadline_day_offset: int = 0  # 0 = evening of processing day P; for ND3 the deadline is early morning


WINDOWS: tuple[WindowDef, ...] = (
    WindowDef("SD1", "Same Day 1", True, time(10, 30), time(12, 0), time(13, 0)),
    WindowDef("SD2", "Same Day 2", True, time(14, 45), time(16, 0), time(17, 0)),
    WindowDef("SD3", "Same Day 3", True, time(16, 45), time(17, 30), time(18, 0)),
    WindowDef("ND1", "Next day (evening)", False, time(20, 0), time(22, 0), time(8, 30)),
    WindowDef("ND2", "Next day (late)", False, time(22, 45), time(23, 30), time(8, 30)),
    WindowDef("ND3", "Next day (last)", False, time(2, 15), time(6, 0), time(8, 30)),
)
WINDOW_BY_CODE = {w.code: w for w in WINDOWS}


@dataclass(frozen=True)
class WindowInstance:
    window: WindowDef
    fed_deadline: datetime        # aware, ET
    internal_cutoff: datetime     # aware, ET
    distribution: datetime
    settlement: datetime          # aware, ET
    settlement_date: date

    @property
    def code(self) -> str:
        return self.window.code

    def to_dict(self) -> dict:
        return {
            "window": self.window.code, "label": self.window.label, "same_day": self.window.same_day,
            "fed_deadline": self.fed_deadline.isoformat(), "internal_cutoff": self.internal_cutoff.isoformat(),
            "distribution": self.distribution.isoformat(), "settlement": self.settlement.isoformat(),
            "settlement_date": self.settlement_date.isoformat(),
        }


def _at(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=ET)


def window_instances(start: date, days: int = 10, cal: FedCalendar = DEFAULT_CALENDAR) -> list[WindowInstance]:
    """Concrete operator windows for calendar days [start, start+days).

    Availability model (the published FedACH holiday schedule overrides it):
    * Same Day windows run only on banking days and settle that day.
    * ND3 (02:15) runs on banking-day mornings and settles 08:30 that day.
    * ND1/ND2 evening windows run on banking days, on Sundays, and on a holiday
      evening when the next day is a banking day (FedACH resumes that evening);
      they settle 08:30 on the next banking day.
    """
    out: list[WindowInstance] = []
    for i in range(days):
        p = start + timedelta(days=i)
        banking = cal.is_banking_day(p)
        evening_open = banking or p.weekday() == 6 or (
            p.weekday() < 5 and cal.is_banking_day(p + timedelta(days=1)))
        for w in WINDOWS:
            if w.same_day:
                if not banking:
                    continue
                s_date = p
                settle = _at(p, w.settlement)
                dist = _at(p, w.distribution)
            elif w.code == "ND3":
                if not banking:
                    continue
                s_date = p
                settle = _at(p, w.settlement)
                dist = _at(p, w.distribution)
            else:
                if not evening_open:
                    continue
                s_date = cal.next_banking_day(p)
                settle = _at(s_date, w.settlement)
                dist = _at(p, w.distribution)
            dl = _at(p, w.fed_deadline)
            out.append(WindowInstance(w, dl, dl - CUTOFF_BUFFER, dist, settle, s_date))
    return sorted(out, key=lambda x: x.fed_deadline)


@dataclass
class SettlementPlan:
    window: WindowInstance
    same_day: bool
    requested_effective_date: date
    effective_date: date           # banking-day adjusted
    stale: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            **self.window.to_dict(),
            "same_day_settlement": self.same_day,
            "requested_effective_date": self.requested_effective_date.isoformat(),
            "effective_date": self.effective_date.isoformat(),
            "stale_dated": self.stale,
            "reasons": self.reasons,
        }


def same_day_eligible(sec_code: str, amount_cents: int, on: date) -> tuple[bool, str | None]:
    sec = SEC_CODES.get(sec_code)
    if sec is None:
        return False, f"Unknown SEC code {sec_code}"
    if not sec.same_day_eligible:
        return False, f"{sec_code} entries are not eligible for Same Day ACH"
    limit = SAME_DAY_LIMIT_CENTS.at(on)
    if amount_cents > limit:
        return False, f"Amount exceeds the Same Day per-entry limit of ${limit / 100:,.0f}"
    return True, None


def plan_settlement(received_at: datetime, effective_date: date, sec_code: str, amount_cents: int,
                    same_day_requested: bool = False, cal: FedCalendar = DEFAULT_CALENDAR) -> SettlementPlan:
    """Pick the operator window an entry will be released in and when it settles."""
    if received_at.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    now = received_at.astimezone(ET)
    today = now.date()
    reasons: list[str] = []
    instances = [w for w in window_instances(today - timedelta(days=1), 14, cal) if w.internal_cutoff >= now]
    if not instances:
        raise RuntimeError("No operator windows available in planning horizon")

    adj = effective_date if cal.is_banking_day(effective_date) else cal.next_banking_day(effective_date)
    if adj != effective_date:
        name = cal.holiday_name(effective_date) or effective_date.strftime("%A")
        reasons.append(f"Effective date {effective_date} is not a banking day ({name}); moved to {adj}")
    stale = adj < today

    target = max(adj, today)
    nd = [w for w in instances if not w.window.same_day]

    if same_day_requested or adj <= today:
        ok, why = same_day_eligible(sec_code, amount_cents, target)
        if ok:
            sd = [w for w in instances if w.window.same_day and w.settlement_date == target]
            if sd:
                if stale:
                    reasons.append("Stale effective date; settles at the next available opportunity")
                return SettlementPlan(sd[0], True, effective_date, adj, stale, reasons)
            reasons.append("Missed the last Same Day cut-off; rolled to next-day settlement")
        elif why:
            reasons.append(why + "; settles in a next-day window")
        if target == today:
            if stale:
                reasons.append("Stale effective date; settles at the next available opportunity")
            return SettlementPlan(nd[0], False, effective_date, adj, stale, reasons)

    # Future-dated: warehouse and release in the first next-day window that settles on the effective date
    on_date = [w for w in nd if w.settlement_date == adj]
    if on_date:
        return SettlementPlan(on_date[0], False, effective_date, adj, False, reasons)
    later = [w for w in nd if w.settlement_date >= adj]
    chosen = later[0] if later else nd[0]
    if chosen.settlement_date != adj:
        reasons.append(f"No window settles on {adj}; settles {chosen.settlement_date}")
    return SettlementPlan(chosen, False, effective_date, adj, False, reasons)


def funds_availability(window_code: str, settlement_date: date, rdfi_tz: str = "America/New_York") -> datetime:
    """When an RDFI must make a credit available to its customer (RDFI local time)."""
    tz = ZoneInfo(rdfi_tz)
    if window_code == "SD1":
        return datetime.combine(settlement_date, time(13, 30), tzinfo=tz)
    if window_code == "SD2":
        return datetime.combine(settlement_date, time(17, 0), tzinfo=tz)
    if window_code == "SD3":
        return datetime.combine(settlement_date, time(23, 59), tzinfo=tz)  # end of processing day
    hhmm = NEXT_DAY_CREDIT_AVAILABILITY.at(settlement_date)
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(settlement_date, time(h, m), tzinfo=tz)


def window_status(now: datetime, cal: FedCalendar = DEFAULT_CALENDAR) -> list[dict]:
    """Today's windows with time remaining — feeds the ops dashboard and ITSM checks."""
    now = now.astimezone(ET)
    rows = []
    for w in window_instances(now.date(), 2, cal):
        if w.fed_deadline.date() != now.date():
            continue
        mins = (w.internal_cutoff - now).total_seconds() / 60
        rows.append({**w.to_dict(), "minutes_to_cutoff": round(mins, 1),
                     "state": "closed" if mins < 0 else ("at_risk" if mins <= 30 else "open")})
    return rows
