from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Alert, CoreAccount, InboundEntry, NocRecord, ReturnRecord, ScreeningResult
from ..services import rdfi as svc
from ..services.common import audit
from .deps import Principal, operator
from .schemas import CoreAccountIn, CoreAccountPatch, InboundDecisionIn, NocIn, UnauthorizedClaimIn

router = APIRouter(prefix="/v1/rdfi", tags=["RDFI (receiving)"])


def _out(e: InboundEntry) -> dict:
    return {"id": e.id, "file_id": e.file_id, "sec_code": e.sec_code, "company_name": e.company_name,
            "company_id": e.company_id, "company_entry_description": e.company_entry_description,
            "direction": e.direction, "transaction_code": e.transaction_code, "amount_cents": e.amount_cents,
            "account_last4": e.account_number[-4:], "individual_name": e.individual_name, "trace_number": e.trace_number,
            "odfi_routing": e.odfi_routing, "settlement_date": e.settlement_date, "window": e.window_code,
            "status": e.status, "funds_available_at": e.funds_available_at, "return_code": e.return_code,
            "risk_score": e.risk_score, "created_at": e.created_at}


@router.get("/entries")
def entries(status: str | None = None, limit: int = 200, _: Principal = Depends(operator), db: Session = Depends(get_db)):
    q = select(InboundEntry).order_by(InboundEntry.created_at.desc()).limit(limit)
    if status:
        q = q.where(InboundEntry.status.in_(status.split(",")))
    return [_out(e) for e in db.scalars(q)]


def _get(db: Session, entry_id: str) -> InboundEntry:
    e = db.get(InboundEntry, entry_id)
    if e is None:
        raise HTTPException(404, {"code": "not_found", "message": "Inbound entry not found"})
    return e


@router.get("/entries/{entry_id}")
def entry(entry_id: str, _: Principal = Depends(operator), db: Session = Depends(get_db)):
    e = _get(db, entry_id)
    return {**_out(e),
            "screening": [{"kind": s.kind, "outcome": s.outcome, "score": s.score, "reasons": s.reasons}
                          for s in db.scalars(select(ScreeningResult).where(ScreeningResult.inbound_entry_id == e.id))],
            "alerts": [{"id": a.id, "kind": a.kind, "status": a.status, "title": a.title}
                       for a in db.scalars(select(Alert).where(Alert.inbound_entry_id == e.id))],
            "returns": [{"code": r.reason_code, "status": r.status} for r in
                        db.scalars(select(ReturnRecord).where(ReturnRecord.inbound_entry_id == e.id))],
            "nocs": [{"code": n.change_code, "corrected_data": n.corrected_data, "status": n.status} for n in
                     db.scalars(select(NocRecord).where(NocRecord.inbound_entry_id == e.id))]}


@router.post("/entries/{entry_id}/decision", description="Release or return a held inbound entry (credit monitoring / sanctions).")
def decide(entry_id: str, body: InboundDecisionIn, p: Principal = Depends(operator), db: Session = Depends(get_db)):
    e = svc.decide_inbound(db, _get(db, entry_id), body.decision, p.actor, body.note)
    db.commit()
    return _out(e)


@router.post("/entries/{entry_id}/unauthorized", description="Customer dispute: re-credit and return R05/R07/R10/R11 (consumer, WSUD) or R29 (business).")
def unauthorized(entry_id: str, body: UnauthorizedClaimIn, p: Principal = Depends(operator), db: Session = Depends(get_db)):
    r = svc.claim_unauthorized(db, _get(db, entry_id), body.return_code, p.actor, body.wsud_on_file)
    db.commit()
    return {"return_id": r.id, "code": r.reason_code, "status": r.status}


@router.post("/entries/{entry_id}/noc", description="Originate a Notification of Change to the ODFI.")
def noc(entry_id: str, body: NocIn, p: Principal = Depends(operator), db: Session = Depends(get_db)):
    n = svc.create_noc(db, _get(db, entry_id), body.change_code, body.corrected_data, p.actor)
    db.commit()
    return {"noc_id": n.id, "status": n.status}


@router.get("/accounts")
def accounts(_: Principal = Depends(operator), db: Session = Depends(get_db)):
    return [{"id": a.id, "account_number": a.account_number, "owner_name": a.owner_name, "owner_type": a.owner_type,
             "account_type": a.account_type, "status": a.status, "ledger_balance_cents": a.ledger_balance_cents,
             "available_balance_cents": a.available_balance_cents, "debit_block": a.debit_block,
             "allowed_debit_company_ids": a.allowed_debit_company_ids, "opened_on": a.opened_on}
            for a in db.scalars(select(CoreAccount).order_by(CoreAccount.owner_name))]


@router.post("/accounts", status_code=201)
def create_account(body: CoreAccountIn, p: Principal = Depends(operator), db: Session = Depends(get_db)):
    a = CoreAccount(account_number=body.account_number, owner_name=body.owner_name, owner_type=body.owner_type,
                    account_type=body.account_type, ledger_balance_cents=body.balance_cents,
                    available_balance_cents=body.balance_cents, debit_block=body.debit_block,
                    allowed_debit_company_ids=body.allowed_debit_company_ids)
    db.add(a)
    audit(db, p.actor, "core_account.created", "core_account", body.account_number)
    db.commit()
    return {"id": a.id}


@router.patch("/accounts/{account_id}")
def patch_account(account_id: str, body: CoreAccountPatch, p: Principal = Depends(operator), db: Session = Depends(get_db)):
    a = db.get(CoreAccount, account_id)
    if a is None:
        raise HTTPException(404, {"code": "not_found", "message": "Account not found"})
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(a, k, v)
    audit(db, p.actor, "core_account.updated", "core_account", a.id, **body.model_dump(exclude_none=True))
    db.commit()
    return {"id": a.id, "status": a.status}
