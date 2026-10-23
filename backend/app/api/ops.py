"""Operations: dashboard, release/settle runs, alerts, cases, incidents, ledger, calendar governance, audit, webhooks."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db, utcnow
from ..domain.windows import ET, UTC, window_status
from ..models import (AchFile, Alert, AuditLog, Case, InboundEntry, Incident, NocRecord, Payment,
                      ReconciliationRun, ReturnRecord, WebhookEvent, WebhookSubscription)
from ..services import calendar_service, ledger, monitoring, scheduler
from ..services.common import DomainError, audit
from ..services.returns import return_rates
from .deps import Principal, operator, principal
from .schemas import (AlertUpdateIn, CalendarOverrideIn, CaseIn, CaseUpdateIn, IncidentUpdateIn, RunIn, WebhookIn)

router = APIRouter(prefix="/v1", tags=["operations"])


@router.get("/ops/dashboard")
def dashboard(_: Principal = Depends(operator), db: Session = Depends(get_db)):
    now = utcnow()
    today = now.astimezone(ET).date()
    by_status = dict(db.execute(select(Payment.status, func.count(Payment.id)).group_by(Payment.status)).all())
    settling = db.execute(select(Payment.direction, func.count(Payment.id), func.coalesce(func.sum(Payment.amount_cents), 0))
                          .where(Payment.settlement_date == today, Payment.status.not_in(["rejected", "cancelled"]))
                          .group_by(Payment.direction)).all()
    by_window = db.execute(select(Payment.window_code, func.count(Payment.id), func.coalesce(func.sum(Payment.amount_cents), 0))
                           .where(Payment.status.in_(["warehoused", "held"])).group_by(Payment.window_code)).all()
    by_sec = db.execute(select(Payment.sec_code, func.count(Payment.id), func.coalesce(func.sum(Payment.amount_cents), 0))
                        .where(Payment.created_at >= now - timedelta(days=30)).group_by(Payment.sec_code)).all()
    recon = db.execute(select(ReconciliationRun.status, func.count(ReconciliationRun.id))
                       .where(ReconciliationRun.at >= now - timedelta(days=1)).group_by(ReconciliationRun.status)).all()
    cal = calendar_service.calendar(db)
    return {
        "as_of": now,
        "today": today, "is_banking_day": cal.is_banking_day(today), "holiday": cal.holiday_name(today),
        "next_banking_day": cal.next_banking_day(today),
        "windows": window_status(now, cal),
        "payments_by_status": by_status,
        "settling_today": {d: {"count": c, "amount_cents": int(a)} for d, c, a in settling},
        "queued_by_window": [{"window": w, "count": c, "amount_cents": int(a)} for w, c, a in by_window],
        "volume_30d_by_sec": [{"sec_code": s, "count": c, "amount_cents": int(a)} for s, c, a in by_sec],
        "open_alerts": db.scalar(select(func.count(Alert.id)).where(Alert.status != "closed")) or 0,
        "held_payments": by_status.get("held", 0),
        "held_inbound": db.scalar(select(func.count(InboundEntry.id)).where(InboundEntry.status == "held")) or 0,
        "open_incidents": db.scalar(select(func.count(Incident.id)).where(Incident.status != "resolved")) or 0,
        "p1_incidents": db.scalar(select(func.count(Incident.id)).where(Incident.status != "resolved", Incident.priority == "P1")) or 0,
        "returns_today": db.scalar(select(func.count(ReturnRecord.id)).where(
            ReturnRecord.at >= datetime.combine(today, datetime.min.time(), tzinfo=ET).astimezone(UTC))) or 0,
        "pending_returns_out": db.scalar(select(func.count(ReturnRecord.id)).where(ReturnRecord.status == "pending")) or 0,
        "reconciliation_24h": dict(recon),
        "ledger_balanced": ledger.trial_balance(db)["balanced"],
        "environment": get_settings().env,
    }


@router.post("/ops/run", description="Run the operations cycle now: release due windows, settle, monitor, deliver webhooks.")
def run(body: RunIn, p: Principal = Depends(operator)):
    s = get_settings()
    if (body.force or body.at) and not s.is_sandbox:
        raise HTTPException(403, {"code": "sandbox_only", "message": "force/at are only available in sandbox"})
    at = body.at.astimezone(UTC) if body.at else None
    return scheduler.tick(now=at, force=body.force, actor=p.actor)


# ------------------------------------------------------------------ alerts & cases
@router.get("/alerts")
def alerts(status: str | None = "open,escalated", kind: str | None = None, limit: int = 200,
           _: Principal = Depends(operator), db: Session = Depends(get_db)):
    q = select(Alert).order_by(Alert.created_at.desc()).limit(limit)
    if status:
        q = q.where(Alert.status.in_(status.split(",")))
    if kind:
        q = q.where(Alert.kind == kind)
    return [{"id": a.id, "kind": a.kind, "severity": a.severity, "status": a.status, "title": a.title,
             "payment_id": a.payment_id, "inbound_entry_id": a.inbound_entry_id, "originator_id": a.originator_id,
             "details": a.details, "assignee": a.assignee, "disposition": a.disposition, "case_id": a.case_id,
             "created_at": a.created_at, "closed_at": a.closed_at} for a in db.scalars(q)]


@router.patch("/alerts/{alert_id}")
def update_alert(alert_id: str, body: AlertUpdateIn, p: Principal = Depends(operator), db: Session = Depends(get_db)):
    a = db.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, {"code": "not_found", "message": "Alert not found"})
    if a.payment_id and body.status == "closed":
        pay = db.get(Payment, a.payment_id)
        if pay and pay.status == "held":
            raise HTTPException(409, {"code": "use_decision", "message": "Release or reject the held payment instead"})
    for k in ("status", "assignee", "disposition", "case_id"):
        v = getattr(body, k)
        if v is not None:
            setattr(a, k, v)
    if body.note:
        a.disposition_note = body.note
    if a.status == "closed":
        a.closed_at = utcnow()
    audit(db, p.actor, "alert.updated", "alert", a.id, **body.model_dump(exclude_none=True))
    db.commit()
    return {"id": a.id, "status": a.status}


@router.post("/cases", status_code=201)
def create_case(body: CaseIn, p: Principal = Depends(operator), db: Session = Depends(get_db)):
    c = Case(title=body.title, subject=body.subject, notes=[{"at": utcnow().isoformat(), "by": p.actor, "note": "Case opened"}])
    db.add(c)
    db.flush()
    for aid in body.alert_ids:
        a = db.get(Alert, aid)
        if a:
            a.case_id, a.status = c.id, "escalated"
    audit(db, p.actor, "case.created", "case", c.id, alerts=body.alert_ids)
    db.commit()
    return {"id": c.id}


@router.get("/cases")
def cases(_: Principal = Depends(operator), db: Session = Depends(get_db)):
    out = []
    for c in db.scalars(select(Case).order_by(Case.created_at.desc())):
        out.append({"id": c.id, "title": c.title, "subject": c.subject, "status": c.status, "notes": c.notes,
                    "sar_reference": c.sar_reference, "created_at": c.created_at,
                    "alerts": [{"id": a.id, "kind": a.kind, "title": a.title} for a in
                               db.scalars(select(Alert).where(Alert.case_id == c.id))]})
    return out


@router.patch("/cases/{case_id}")
def update_case(case_id: str, body: CaseUpdateIn, p: Principal = Depends(operator), db: Session = Depends(get_db)):
    c = db.get(Case, case_id)
    if c is None:
        raise HTTPException(404, {"code": "not_found", "message": "Case not found"})
    note = {"at": utcnow().isoformat(), "by": p.actor, "note": body.note or body.action}
    c.notes = [*c.notes, note]
    if body.action == "file_sar":
        if not body.sar_reference:
            raise HTTPException(422, {"code": "sar_reference", "message": "BSA E-Filing reference required"})
        c.status, c.sar_reference = "sar_filed", body.sar_reference
    elif body.action == "close":
        c.status = "closed_no_action" if c.status == "open" else c.status
        for a in db.scalars(select(Alert).where(Alert.case_id == c.id, Alert.status != "closed")):
            a.status, a.closed_at = "closed", utcnow()
    audit(db, p.actor, f"case.{body.action}", "case", c.id)
    db.commit()
    return {"id": c.id, "status": c.status}


# ------------------------------------------------------------------ incidents
@router.get("/incidents")
def incidents(status: str | None = None, _: Principal = Depends(operator), db: Session = Depends(get_db)):
    q = select(Incident).order_by(Incident.opened_at.desc())
    if status:
        q = q.where(Incident.status.in_(status.split(",")))
    return [{"id": i.id, "priority": i.priority, "category": i.category, "title": i.title, "status": i.status,
             "details": i.details, "external_ref": i.external_ref, "opened_at": i.opened_at,
             "acknowledged_at": i.acknowledged_at, "resolved_at": i.resolved_at, "resolution": i.resolution}
            for i in db.scalars(q)]


@router.patch("/incidents/{incident_id}")
def update_incident(incident_id: str, body: IncidentUpdateIn, p: Principal = Depends(operator), db: Session = Depends(get_db)):
    i = db.get(Incident, incident_id)
    if i is None:
        raise HTTPException(404, {"code": "not_found", "message": "Incident not found"})
    monitoring.update_incident(db, i, body.action, p.actor, body.note)
    audit(db, p.actor, f"incident.{body.action}", "incident", i.id, note=body.note)
    db.commit()
    return {"id": i.id, "status": i.status}


# ------------------------------------------------------------------ returns, NOCs, settlement, ledger
@router.get("/returns")
def returns(direction: str | None = None, limit: int = 200, _: Principal = Depends(operator), db: Session = Depends(get_db)):
    q = select(ReturnRecord).order_by(ReturnRecord.at.desc()).limit(limit)
    if direction:
        q = q.where(ReturnRecord.direction == direction)
    return [{"id": r.id, "direction": r.direction, "reason_code": r.reason_code, "category": r.category,
             "payment_id": r.payment_id, "inbound_entry_id": r.inbound_entry_id, "originator_id": r.originator_id,
             "original_trace": r.original_trace, "amount_cents": r.amount_cents, "deadline": r.deadline,
             "timely": r.timely, "status": r.status, "at": r.at} for r in db.scalars(q)]


@router.get("/nocs")
def nocs(_: Principal = Depends(operator), db: Session = Depends(get_db)):
    return [{"id": n.id, "direction": n.direction, "change_code": n.change_code, "corrected_data": n.corrected_data,
             "payment_id": n.payment_id, "counterparty_id": n.counterparty_id, "inbound_entry_id": n.inbound_entry_id,
             "original_trace": n.original_trace, "apply_by": n.apply_by, "applied_at": n.applied_at,
             "status": n.status, "at": n.at} for n in db.scalars(select(NocRecord).order_by(NocRecord.at.desc()))]


@router.get("/settlements")
def settlements(_: Principal = Depends(operator), db: Session = Depends(get_db)):
    return [{"id": r.id, "file_id": r.file_id, "settlement_date": r.settlement_date, "window": r.window_code,
             "advice_net_cents": r.advice_net_cents, "ledger_net_cents": r.ledger_net_cents,
             "core_net_cents": r.core_net_cents, "status": r.status, "at": r.at}
            for r in db.scalars(select(ReconciliationRun).order_by(ReconciliationRun.at.desc()))]


@router.get("/ledger/trial-balance")
def trial_balance(_: Principal = Depends(operator), db: Session = Depends(get_db)):
    return ledger.trial_balance(db)


# ------------------------------------------------------------------ calendar governance (maker-checker)
@router.post("/calendar/overrides", status_code=201)
def request_override(body: CalendarOverrideIn, p: Principal = Depends(operator), db: Session = Depends(get_db)):
    row = calendar_service.request_override(db, body.day, body.kind, body.note, p.actor)
    db.commit()
    return {"day": row.day, "kind": row.kind, "approved": False}


@router.post("/calendar/overrides/{day}/approve")
def approve_override(day: str, p: Principal = Depends(operator), db: Session = Depends(get_db)):
    from datetime import date as _d
    row = calendar_service.approve_override(db, _d.fromisoformat(day), p.actor)
    db.commit()
    return {"day": row.day, "kind": row.kind, "approved_by": row.approved_by}


# ------------------------------------------------------------------ webhooks & audit
@router.post("/webhooks", status_code=201)
def create_webhook(body: WebhookIn, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    oid = body.originator_id if p.is_operator else p.originator_id
    w = WebhookSubscription(originator_id=oid, url=body.url, events=body.events, secret="whsec_" + secrets.token_hex(16))
    db.add(w)
    db.commit()
    return {"id": w.id, "secret": w.secret, "events": w.events}


@router.get("/webhooks/events")
def webhook_events(limit: int = Query(100, le=500), p: Principal = Depends(principal), db: Session = Depends(get_db)):
    q = select(WebhookEvent).order_by(WebhookEvent.created_at.desc()).limit(limit)
    if not p.is_operator:
        q = q.where(WebhookEvent.originator_id == p.originator_id)
    return [{"id": e.id, "type": e.event_type, "status": e.status, "attempts": e.attempts,
             "last_error": e.last_error, "created_at": e.created_at, "payload": e.payload} for e in db.scalars(q)]


@router.get("/audit")
def audit_log(entity: str | None = None, entity_id: str | None = None, limit: int = Query(200, le=1000),
              _: Principal = Depends(operator), db: Session = Depends(get_db)):
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if entity:
        q = q.where(AuditLog.entity == entity)
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    return [{"at": a.at, "actor": a.actor, "action": a.action, "entity": a.entity, "entity_id": a.entity_id,
             "detail": a.detail} for a in db.scalars(q)]
