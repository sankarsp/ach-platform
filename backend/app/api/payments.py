from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Alert, IdempotencyRecord, Payment, PaymentEvent, ScreeningResult
from ..services import originations as svc
from .deps import Principal, principal, scoped_originator
from .schemas import DecisionIn, PaymentIn, PaymentOut, ReversalIn

router = APIRouter(prefix="/v1/payments", tags=["payments"])


def _get(db: Session, p: Principal, payment_id: str) -> Payment:
    pay = db.get(Payment, payment_id)
    if pay is None or (not p.is_operator and pay.originator_id != p.originator_id):
        raise HTTPException(404, {"code": "not_found", "message": "Payment not found"})
    return pay


@router.post("", status_code=201, response_model=PaymentOut,
             summary="Create an ACH credit or debit",
             description="Validates against the Nacha rules catalog, plans the FedACH window and settlement date, "
                         "screens sanctions/fraud/AML, then warehouses or holds the entry. Requires Idempotency-Key.")
def create(body: PaymentIn, idempotency_key: str = Header(..., alias="Idempotency-Key", max_length=120),
           p: Principal = Depends(principal), db: Session = Depends(get_db)):
    req_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    prior = db.get(IdempotencyRecord, (p.client.id, idempotency_key))
    if prior:
        if prior.request_hash != req_hash:
            raise HTTPException(409, {"code": "idempotency_conflict",
                                      "message": "Idempotency-Key reused with a different request body"})
        return JSONResponse(prior.response, status_code=prior.status_code, headers={"Idempotent-Replayed": "true"})
    org = scoped_originator(db, p, body.originator_id)
    req = svc.PaymentRequest(counterparty_id=body.counterparty_id, amount_cents=body.amount_cents,
                             direction=body.direction, sec_code=body.sec_code, effective_date=body.effective_date,
                             purpose=body.purpose, company_entry_description=body.company_entry_description,
                             same_day=body.same_day, addenda=body.addenda, web_payment_type=body.web_payment_type,
                             iat=body.iat.model_dump() if body.iat else None, idempotency_key=idempotency_key,
                             metadata=body.metadata)
    pay = svc.create_payment(db, org, req, p.actor)
    out = PaymentOut.model_validate(pay)
    db.add(IdempotencyRecord(client_id=p.client.id, key=idempotency_key, request_hash=req_hash, status_code=201,
                             response=json.loads(out.model_dump_json())))
    db.commit()
    return out


@router.get("", response_model=list[PaymentOut])
def list_payments(status: str | None = None, originator_id: str | None = None, sec_code: str | None = None,
                  window: str | None = None, settlement_date: str | None = None,
                  limit: int = Query(100, le=500), offset: int = 0,
                  p: Principal = Depends(principal), db: Session = Depends(get_db)):
    q = select(Payment)
    if not p.is_operator:
        q = q.where(Payment.originator_id == p.originator_id)
    elif originator_id:
        q = q.where(Payment.originator_id == originator_id)
    if status:
        q = q.where(Payment.status.in_(status.split(",")))
    if sec_code:
        q = q.where(Payment.sec_code == sec_code)
    if window:
        q = q.where(Payment.window_code == window)
    if settlement_date:
        q = q.where(Payment.settlement_date == settlement_date)
    return db.scalars(q.order_by(Payment.created_at.desc()).limit(limit).offset(offset)).all()


@router.get("/{payment_id}")
def get_payment(payment_id: str, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    pay = _get(db, p, payment_id)
    events = db.scalars(select(PaymentEvent).where(PaymentEvent.payment_id == pay.id).order_by(PaymentEvent.id)).all()
    out = PaymentOut.model_validate(pay).model_dump()
    out["counterparty"] = {"id": pay.counterparty.id, "name": pay.counterparty.name,
                           "routing_number": pay.counterparty.routing_number,
                           "account_last4": pay.counterparty.account_last4, "party_type": pay.counterparty.party_type}
    out["originator"] = {"id": pay.originator.id, "name": pay.originator.name, "company_id": pay.originator.company_id}
    out["addenda"] = pay.addenda
    out["iat"] = pay.iat
    out["events"] = [{"at": e.at, "from": e.from_status, "to": e.to_status, "actor": e.actor, "detail": e.detail}
                     for e in events]
    if p.is_operator:
        out["screening"] = [{"kind": s.kind, "outcome": s.outcome, "score": s.score, "reasons": s.reasons, "at": s.at}
                            for s in db.scalars(select(ScreeningResult).where(ScreeningResult.payment_id == pay.id))]
        out["alerts"] = [{"id": a.id, "kind": a.kind, "severity": a.severity, "status": a.status, "title": a.title}
                         for a in db.scalars(select(Alert).where(Alert.payment_id == pay.id))]
    return out


@router.post("/{payment_id}/cancel", response_model=PaymentOut)
def cancel(payment_id: str, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    pay = svc.cancel_payment(db, _get(db, p, payment_id), p.actor)
    db.commit()
    return pay


@router.post("/{payment_id}/reversal", response_model=PaymentOut, status_code=201,
             description="Nacha-permissible reversal (duplicate, wrong receiver, wrong amount, wrong date) within 5 banking days.")
def reversal(payment_id: str, body: ReversalIn, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    rev = svc.create_reversal(db, _get(db, p, payment_id), body.reason, p.actor)
    db.commit()
    return rev


@router.post("/{payment_id}/decision", response_model=PaymentOut,
             description="Operator: release or reject a held payment. Closes its open alerts.")
def decide(payment_id: str, body: DecisionIn, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    if not p.is_operator:
        raise HTTPException(403, {"code": "forbidden", "message": "Operator role required"})
    pay = svc.decide_hold(db, _get(db, p, payment_id), body.decision, p.actor, body.note)
    db.commit()
    return pay
