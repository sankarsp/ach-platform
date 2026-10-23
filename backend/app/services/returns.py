"""ODFI side: inbound returns, Notifications of Change and return-rate monitoring."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..domain import rules as RL
from ..domain.windows import ET
from ..models import AchFile, Alert, Counterparty, NocRecord, Originator, Payment, ReturnRecord
from ..nacha.file import parse_file
from . import core_banking, ledger
from .calendar_service import calendar
from .common import DomainError, audit, emit, set_status, tokenize
from .monitoring import open_incident
from .originations import originator_funding_account


def return_deadline(db: Session, settlement_date: date, code: str) -> date:
    rc = RL.RETURN_CODES[code]
    if rc.window_unit == "calendar":
        return settlement_date + timedelta(days=rc.window)
    return calendar(db).add_banking_days(settlement_date, rc.window)


def ingest_inbound_file(db: Session, text: str, actor: str, now: datetime | None = None) -> dict:
    """Entry point for any file received from FedACH. Routes returns/NOCs to the
    ODFI handlers and forward entries to the RDFI receiver."""
    from .rdfi import receive_forward_entries
    now = now or utcnow()
    sha = hashlib.sha256(text.encode()).hexdigest()
    if db.scalar(select(AchFile.id).where(AchFile.sha256 == sha)):
        open_incident(db, f"dup_file:{sha[:16]}", "P2", "duplicate_file", "Duplicate inbound file rejected", {"sha256": sha})
        raise DomainError("duplicate_file", "This file has already been received", 409)
    pf = parse_file(text)
    f = AchFile(direction="inbound", kind="mixed", content=text, sha256=sha, status="received",
                entry_count=len(pf.entries), errors=pf.errors,
                total_debit_cents=int(pf.control.get("total_debit") or 0),
                total_credit_cents=int(pf.control.get("total_credit") or 0),
                settlement_date=now.astimezone(ET).date(), settlement_at=now)
    db.add(f)
    db.flush()
    if pf.errors:
        f.status = "rejected"
        open_incident(db, f"bad_file:{f.id}", "P1", "file_rejected", "Inbound file failed validation", {"errors": pf.errors[:10]})
        audit(db, actor, "file.rejected", "ach_file", f.id, errors=pf.errors[:10])
        return {"file_id": f.id, "status": "rejected", "errors": pf.errors}

    summary = {"file_id": f.id, "returns": 0, "nocs": 0, "received": 0, "unmatched": 0}
    forward = []
    for b in pf.batches:
        for e in b["entries"]:
            types = {a["addenda_type"] for a in e["addenda"]}
            if "99" in types:
                summary["returns" if process_return(db, e, f, now) else "unmatched"] += 1
            elif "98" in types:
                summary["nocs" if process_noc(db, e, now) else "unmatched"] += 1
            else:
                forward.append((b, e))
    if forward:
        summary["received"] = receive_forward_entries(db, f, forward, now)
    f.status = "processed"
    audit(db, actor, "file.received", "ach_file", f.id, **summary)
    return summary


def process_return(db: Session, e: dict, f: AchFile, now: datetime) -> bool:
    a = next(x for x in e["addenda"] if x["addenda_type"] == "99")
    code, original_trace = a["return_reason_code"], a["original_trace"]
    p = db.scalar(select(Payment).where(Payment.trace_number == original_trace))
    if p is None:
        open_incident(db, f"unmatched_return:{original_trace}", "P3", "unmatched_return",
                      f"Return {code} for unknown trace {original_trace}", {"trace": original_trace})
        return False
    if db.scalar(select(ReturnRecord.id).where(ReturnRecord.payment_id == p.id, ReturnRecord.direction == "received")):
        return True   # duplicate return; a dishonor (R67) would be raised from the ops console
    rc = RL.RETURN_CODES.get(code)
    if rc is None:
        rc = RL.ReturnCode(code, "Unknown return code", RL.ReturnCategory.OTHER, 2, "banking")
    if p.status == "transmitted":
        from .release import settle_payment
        settle_payment(db, p, db.get(AchFile, p.file_id), now, "fedach")
    deadline = return_deadline(db, p.settlement_date, code) if code in RL.RETURN_CODES else None
    timely = deadline is None or now.astimezone(ET).date() <= deadline
    rr = ReturnRecord(direction="received", payment_id=p.id, originator_id=p.originator_id, reason_code=code,
                      category=rc.category.value, original_trace=original_trace, amount_cents=p.amount_cents,
                      deadline=deadline, timely=timely, file_id=f.id, addenda_information=a.get("addenda_information", ""))
    db.add(rr)
    org = p.originator
    if p.amount_cents:
        if p.direction == "credit":   # funds come back to the originator
            lines = [("FRB_SETTLEMENT", p.amount_cents, 0), (f"ORIG:{org.id}", 0, p.amount_cents)]
            core_amt = p.amount_cents
        else:                          # collected funds are taken back from the originator
            lines = [(f"ORIG:{org.id}", p.amount_cents, 0), ("FRB_SETTLEMENT", 0, p.amount_cents)]
            core_amt = -p.amount_cents
        ledger.post(db, f"return:{p.id}", "return_received", f"Return {code} on {original_trace}", lines,
                    payment_id=p.id, file_id=f.id)
        acct = originator_funding_account(db, org)
        if acct:
            core_banking.post(db, acct, core_amt, f"pay:{p.id}:return", f"ACH return {code} {original_trace}")
    p.return_code, p.returned_at = code, now
    set_status(db, p, "returned", "fedach", reason=f"{code} {rc.description}")

    cp = db.get(Counterparty, p.counterparty_id)
    if code in ("R02", "R03", "R04"):
        cp.validation_status, cp.status, cp.block_reason = "failed", "blocked", f"{code} {rc.description}"
    elif code in ("R05", "R07", "R10", "R11", "R16", "R29"):
        cp.status, cp.block_reason = "blocked", f"{code} {rc.description}: new authorization required"
    if not timely:
        db.add(Alert(kind="untimely_return", severity="medium", title=f"Untimely return {code} on {original_trace}",
                     payment_id=p.id, originator_id=org.id,
                     details={"deadline": str(deadline), "received": str(now.date()), "action": "Consider dishonor (R68)"}))
    check_return_rates(db, org, now)
    return True


def process_noc(db: Session, e: dict, now: datetime) -> bool:
    a = next(x for x in e["addenda"] if x["addenda_type"] == "98")
    p = db.scalar(select(Payment).where(Payment.trace_number == a["original_trace"]))
    if p is None:
        return False
    cp = db.get(Counterparty, p.counterparty_id)
    code, data = a["change_code"], a["corrected_data"]
    apply_by = calendar(db).add_banking_days(now.astimezone(ET).date(), RL.NOC_APPLY_BANKING_DAYS)
    n = NocRecord(direction="received", payment_id=p.id, counterparty_id=cp.id, originator_id=p.originator_id,
                  change_code=code, corrected_data=data, original_trace=a["original_trace"], apply_by=apply_by)
    db.add(n)
    apply_noc(db, cp, code, data)
    n.status, n.applied_at = "applied", now
    emit(db, p.originator_id, "noc.received", {"payment_id": p.id, "counterparty_id": cp.id, "change_code": code,
                                                "description": RL.NOC_CODES.get(code, ("",))[0], "applied": True})
    audit(db, "fedach", "noc.applied", "counterparty", cp.id, change_code=code)
    return True


def _acct_type_from_txn(code: str) -> str:
    return {"2": "checking", "3": "savings", "4": "gl", "5": "loan"}.get(code[:1], "checking")


def apply_noc(db: Session, cp: Counterparty, code: str, data: str) -> None:
    """Apply corrected data per the Nacha NOC corrected-data layouts. NOC-driven changes
    do not trip the 'bank details changed' fraud signal — they come from the RDFI."""
    d = data
    if code == "C01":
        acct = d.strip()
        cp.account_token, cp.account_last4 = tokenize(db, acct), acct[-4:]
    elif code == "C02":
        cp.routing_number = d[:9]
    elif code == "C03":
        cp.routing_number = d[:9]
        acct = d[12:29].strip()
        cp.account_token, cp.account_last4 = tokenize(db, acct), acct[-4:]
    elif code == "C05":
        cp.account_type = _acct_type_from_txn(d[:2])
    elif code == "C06":
        acct = d[:17].strip()
        cp.account_token, cp.account_last4 = tokenize(db, acct), acct[-4:]
        cp.account_type = _acct_type_from_txn(d[20:22])
    elif code == "C07":
        cp.routing_number = d[:9]
        acct = d[9:26].strip()
        cp.account_token, cp.account_last4 = tokenize(db, acct), acct[-4:]
        cp.account_type = _acct_type_from_txn(d[26:28])
    elif code == "C09":
        cp.individual_id = d[:15].strip()


def return_rates(db: Session, originator_id: str, now: datetime) -> dict:
    """Nacha return-rate monitoring, rolling 60 days, debit entries."""
    since = now - timedelta(days=60)
    debits = db.scalar(select(func.count(Payment.id)).where(
        Payment.originator_id == originator_id, Payment.direction == "debit", Payment.amount_cents > 0,
        Payment.transmitted_at >= since)) or 0
    rows = db.execute(select(ReturnRecord.category, func.count(ReturnRecord.id))
                      .join(Payment, Payment.id == ReturnRecord.payment_id)
                      .where(ReturnRecord.originator_id == originator_id, ReturnRecord.direction == "received",
                             Payment.direction == "debit", ReturnRecord.at >= since)
                      .group_by(ReturnRecord.category)).all()
    by = {c: n for c, n in rows}
    total = sum(by.values())
    th = RL.RETURN_RATE_THRESHOLDS.at(now.date())
    def rate(n): return (n / debits) if debits else 0.0
    out = {"debit_entries": debits, "unauthorized": rate(by.get("unauthorized", 0)),
           "administrative": rate(by.get("administrative", 0)), "overall": rate(total), "thresholds": th}
    out["breaches"] = [k for k in ("unauthorized", "administrative", "overall") if out[k] > th[k]]
    return out


def check_return_rates(db: Session, org: Originator, now: datetime) -> dict:
    rates = return_rates(db, org.id, now)
    if rates["breaches"] and rates["debit_entries"] >= 20:   # avoid noise on tiny volumes
        key = f"return_rate:{org.id}:{now.date()}"
        open_incident(db, key, "P3", "return_rate_breach", f"{org.name} breached return-rate thresholds",
                      {"breaches": rates["breaches"], "unauthorized": f"{rates['unauthorized']:.2%}",
                       "administrative": f"{rates['administrative']:.2%}", "overall": f"{rates['overall']:.2%}"})
        if not db.scalar(select(Alert.id).where(Alert.kind == "return_rate", Alert.originator_id == org.id,
                                                Alert.status == "open")):
            db.add(Alert(kind="return_rate", severity="high", title=f"Return-rate threshold breach: {org.name}",
                         originator_id=org.id, details=rates))
    return rates
