"""Fed calendar as governed data: computed holidays plus approved overrides (four-eyes)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.fed_calendar import FedCalendar
from ..models import CalendarOverride
from .common import DomainError, audit


def calendar(db: Session) -> FedCalendar:
    rows = db.scalars(select(CalendarOverride).where(CalendarOverride.approved_by.is_not(None))).all()
    return FedCalendar(extra_closures={r.day for r in rows if r.kind == "closure"},
                       extra_openings={r.day for r in rows if r.kind == "opening"})


def request_override(db: Session, day, kind: str, note: str, actor: str) -> CalendarOverride:
    if kind not in ("closure", "opening"):
        raise DomainError("bad_kind", "kind must be closure or opening")
    row = db.get(CalendarOverride, day) or CalendarOverride(day=day)
    row.kind, row.note, row.requested_by, row.approved_by = kind, note, actor, None
    db.add(row)
    audit(db, actor, "calendar.override_requested", "calendar", str(day), kind=kind, note=note)
    return row


def approve_override(db: Session, day, actor: str) -> CalendarOverride:
    row = db.get(CalendarOverride, day)
    if row is None:
        raise DomainError("not_found", "No override for that date", 404)
    if row.requested_by == actor:
        raise DomainError("four_eyes", "A different user must approve a calendar change", 403)
    row.approved_by = actor
    audit(db, actor, "calendar.override_approved", "calendar", str(day))
    return row
