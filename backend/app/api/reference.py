from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..domain import rules as RL
from ..domain.fed_calendar import fed_holidays
from ..domain.windows import CUTOFF_BUFFER, ET, WINDOWS, plan_settlement, window_status
from ..services.calendar_service import calendar
from ..services.originations import USE_CASES
from .deps import Principal, principal
from .schemas import PreviewIn

router = APIRouter(prefix="/v1/reference", tags=["reference"])


@router.get("/sec-codes")
def sec_codes(_: Principal = Depends(principal)):
    return [{"code": s.code, "name": s.name, "receiver": s.receiver.value, "credits": s.credits, "debits": s.debits,
             "max_addenda": s.max_addenda, "same_day_eligible": s.same_day_eligible, "authorization": s.authorization,
             "use_cases": list(s.use_cases)} for s in RL.SEC_CODES.values()]


@router.get("/use-cases")
def use_cases(_: Principal = Depends(principal)):
    return USE_CASES


@router.get("/return-codes")
def return_codes(_: Principal = Depends(principal)):
    return [{"code": r.code, "description": r.description, "category": r.category.value, "window": r.window,
             "window_unit": r.window_unit, "requires_wsud": r.requires_wsud} for r in RL.RETURN_CODES.values()]


@router.get("/noc-codes")
def noc_codes(_: Principal = Depends(principal)):
    return [{"code": k, "description": v[0], "fields": v[1]} for k, v in RL.NOC_CODES.items()]


@router.get("/rules")
def rules(on: date | None = None, _: Principal = Depends(principal)):
    return RL.rules_snapshot(on or utcnow().astimezone(ET).date())


@router.get("/windows")
def windows(_: Principal = Depends(principal), db: Session = Depends(get_db)):
    return {"schedule": [{"window": w.code, "label": w.label, "same_day": w.same_day,
                          "fed_deadline_et": w.fed_deadline.strftime("%H:%M"),
                          "internal_cutoff_et": ((datetime.combine(date.today(), w.fed_deadline) - CUTOFF_BUFFER).time().strftime("%H:%M")),
                          "distribution_et": w.distribution.strftime("%H:%M"),
                          "settlement_et": w.settlement.strftime("%H:%M"),
                          "settles": "same day" if w.same_day else ("that banking day" if w.code == "ND3" else "next banking day")}
                         for w in WINDOWS],
            "today": window_status(utcnow(), calendar(db))}


@router.get("/calendar")
def fed_calendar(start: date | None = None, end: date | None = None, _: Principal = Depends(principal),
                 db: Session = Depends(get_db)):
    cal = calendar(db)
    start = start or utcnow().astimezone(ET).date()
    end = end or start + timedelta(days=455)
    years = range(start.year, end.year + 1)
    return {
        "start": start, "end": end,
        "holidays": [{"name": h.name, "date": h.date, "observed": h.observed, "weekday": h.date.strftime("%A"),
                      "fed_closed_on": h.observed, "note": ("Saturday holiday: Reserve Banks open the preceding Friday"
                                                            if h.observed is None else
                                                            ("Sunday holiday: closed Monday" if h.observed != h.date else ""))}
                     for y in years for h in fed_holidays(y) if start <= h.date <= end],
        "non_settlement_days": cal.non_settlement_days(start, end),
        "overrides": {"closures": sorted(cal.extra_closures), "openings": sorted(cal.extra_openings)},
    }


@router.post("/settlement-preview",
             description="Which FedACH window an entry would go in and when it would settle, given cut-offs, weekends and holidays.")
def settlement_preview(body: PreviewIn, _: Principal = Depends(principal), db: Session = Depends(get_db)):
    at = body.at or utcnow()
    plan = plan_settlement(at, body.effective_date, body.sec_code, body.amount_cents, body.same_day, calendar(db))
    return {"received_at": at, **plan.to_dict()}


@router.get("/business-days")
def business_days(start: date, n: int = 2, _: Principal = Depends(principal), db: Session = Depends(get_db)):
    cal = calendar(db)
    return {"start": start, "banking_days": n, "result": cal.add_banking_days(start, n),
            "is_banking_day": cal.is_banking_day(start)}
