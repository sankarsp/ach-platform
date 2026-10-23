"""File channel: clients upload Nacha files; operators view outbound/inbound files and ingest FedACH files."""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..domain import rules as RL
from ..models import AchFile, Counterparty, Payment, ReconciliationRun
from ..nacha.file import from_yymmdd, parse_file
from ..services import originations as svc
from ..services.common import DomainError, detokenize
from ..services.returns import ingest_inbound_file
from .deps import Principal, operator, principal, scoped_originator
from .schemas import FileIn

router = APIRouter(prefix="/v1/files", tags=["files"])


def _summary(f: AchFile) -> dict:
    return {"id": f.id, "direction": f.direction, "kind": f.kind, "window": f.window_code, "status": f.status,
            "file_id_modifier": f.file_id_modifier, "entry_count": f.entry_count,
            "total_debit_cents": f.total_debit_cents, "total_credit_cents": f.total_credit_cents,
            "settlement_date": f.settlement_date, "settlement_at": f.settlement_at, "created_at": f.created_at,
            "transmitted_at": f.transmitted_at, "settled_at": f.settled_at, "errors": f.errors, "sha256": f.sha256}


@router.get("")
def list_files(direction: str | None = None, limit: int = 100, _: Principal = Depends(operator), db: Session = Depends(get_db)):
    q = select(AchFile).order_by(AchFile.created_at.desc()).limit(limit)
    if direction:
        q = q.where(AchFile.direction == direction)
    files = db.scalars(q).all()
    recon = {r.file_id: r.status for r in db.scalars(select(ReconciliationRun).where(
        ReconciliationRun.file_id.in_([f.id for f in files])))}
    return [{**_summary(f), "reconciliation": recon.get(f.id)} for f in files]


@router.get("/{file_id}")
def get_file(file_id: str, _: Principal = Depends(operator), db: Session = Depends(get_db)):
    f = db.get(AchFile, file_id)
    if f is None:
        raise HTTPException(404, {"code": "not_found", "message": "File not found"})
    pf = parse_file(f.content)
    return {**_summary(f), "batches": [{"header": b["header"], "control": b["control"], "entries": len(b["entries"])}
                                       for b in pf.batches],
            "validation_errors": pf.errors}


@router.get("/{file_id}/raw", response_class=PlainTextResponse)
def raw(file_id: str, _: Principal = Depends(operator), db: Session = Depends(get_db)):
    f = db.get(AchFile, file_id)
    if f is None:
        raise HTTPException(404, {"code": "not_found", "message": "File not found"})
    return f.content


@router.post("/inbound", description="Ingest a file received from FedACH: forward entries (RDFI), returns and NOCs (ODFI).")
def inbound(body: FileIn, p: Principal = Depends(operator), db: Session = Depends(get_db)):
    out = ingest_inbound_file(db, body.content, p.actor)
    db.commit()
    return out


@router.post("/validate", description="Validate a Nacha file without processing it.")
def validate(body: FileIn, _: Principal = Depends(principal)):
    pf = parse_file(body.content)
    return {"valid": not pf.errors, "errors": pf.errors, "batches": len(pf.batches), "entries": len(pf.entries),
            "total_debit_cents": int(pf.control.get("total_debit") or 0),
            "total_credit_cents": int(pf.control.get("total_credit") or 0)}


@router.post("/originate", status_code=201,
             description="Originator file channel: submit a Nacha file; each entry becomes a payment through the same controls as the API.")
def originate(body: FileIn, originator_id: str | None = None, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    org = scoped_originator(db, p, originator_id)
    pf = parse_file(body.content)
    if pf.errors:
        raise HTTPException(422, {"code": "invalid_file", "message": "File failed validation", "errors": pf.errors[:20]})
    sha = hashlib.sha256(body.content.encode()).hexdigest()[:16]
    results = []
    for b in pf.batches:
        hdr, sec = b["header"], b["sec_code"]
        if sec == "IAT":
            results.append({"batch": hdr.get("batch_number"), "status": "rejected",
                            "error": "Submit IAT through the API so party data can be screened"})
            continue
        eff = from_yymmdd(hdr.get("effective_entry_date", ""))
        for e in b["entries"]:
            txn = e["transaction_code"]
            direction = "credit" if RL.is_credit(txn) else "debit"
            acct = e["dfi_account_number"].strip()
            rtn = e["rdfi_identification"] + e["check_digit"]
            cp = next((c for c in db.scalars(select(Counterparty).where(
                Counterparty.originator_id == org.id, Counterparty.routing_number == rtn))
                if detokenize(db, c.account_token) == acct), None)
            sp = db.begin_nested()     # each entry is atomic; one bad entry doesn't sink the file
            try:
                if cp is None:
                    cp = svc.create_counterparty(db, org, {
                        "name": e["individual_name"] or "Receiver", "party_type": "business" if sec in ("CCD", "CTX") else "consumer",
                        "routing_number": rtn, "account_number": acct,
                        "account_type": "savings" if txn[0] == "3" else "checking",
                        "individual_id": e.get("individual_id", "")}, p.actor)
                key = f"file:{sha}:{e['trace_number']}"
                existing = db.scalar(select(Payment).where(Payment.originator_id == org.id, Payment.idempotency_key == key))
                if existing:
                    sp.commit()
                    results.append({"trace": e["trace_number"], "payment_id": existing.id, "status": existing.status, "replayed": True})
                    continue
                desc = hdr.get("company_entry_description", "")
                purpose = "payroll" if desc.strip() == "PAYROLL" else ("purchase" if desc.strip() == "PURCHASE" else "other")
                pay = svc.create_payment(db, org, svc.PaymentRequest(
                    counterparty_id=cp.id, amount_cents=int(e["amount"]), direction=direction, sec_code=sec,
                    effective_date=eff, purpose=purpose, company_entry_description=desc,
                    addenda=[a.get("payment_related_information", "") for a in e["addenda"] if a["addenda_type"] == "05"],
                    idempotency_key=key, prenote=txn in ("23", "28", "33", "38")), p.actor)
                sp.commit()
                results.append({"trace": e["trace_number"], "payment_id": pay.id, "status": pay.status})
            except DomainError as exc:
                sp.rollback()
                results.append({"trace": e["trace_number"], "status": "rejected", "error": exc.message, "code": exc.code})
    db.commit()
    accepted = sum(1 for r in results if r.get("payment_id"))
    return {"accepted": accepted, "rejected": len(results) - accepted, "results": results}
