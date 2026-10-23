"""RDFI side: receive forward entries, post to customers, credit monitoring,
funds availability, automated returns/NOCs and unauthorized-entry claims."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..domain import rules as RL
from ..domain.windows import ET, UTC, funds_availability
from ..models import AchFile, Alert, CoreAccount, CorePosting, InboundEntry, NocRecord, ReturnRecord
from ..nacha.builders import noc_entry, return_entry
from ..nacha.file import Batch, from_yymmdd
from . import core_banking, ledger, screening
from .calendar_service import calendar
from .common import DomainError, audit

CONSUMER_UNAUTHORIZED = {"R05", "R07", "R10", "R11"}


def _window_from_batch(hdr: dict) -> str:
    dd = (hdr.get("company_descriptive_date") or "").strip()
    return {"SD1300": "SD1", "SD1700": "SD2", "SD1800": "SD3"}.get(dd, "ND1")


def _settlement_date(db: Session, hdr: dict, now: datetime) -> date:
    cal = calendar(db)
    today = now.astimezone(ET).date()
    eff = from_yymmdd(hdr.get("effective_entry_date", ""))
    if eff is None or eff < today:
        eff = today
    return eff if cal.is_banking_day(eff) else cal.next_banking_day(eff)


def receive_forward_entries(db: Session, f: AchFile, forward: list[tuple[dict, dict]], now: datetime) -> int:
    s = get_settings()
    n = 0
    for b, e in forward:
        hdr = b["header"]
        sec = b["sec_code"]
        direction = "credit" if RL.is_credit(e["transaction_code"]) else "debit"
        if e["transaction_code"] in ("23", "28", "33", "38"):
            direction = "credit" if e["transaction_code"] in ("23", "33") else "debit"
        settle = _settlement_date(db, hdr, now)
        window = _window_from_batch(hdr)
        name = e.get("individual_name", "")
        if sec == "IAT":
            a10 = next((a for a in e["addenda"] if a["addenda_type"] == "10"), {})
            name = a10.get("receiver_name", "")
        ie = InboundEntry(file_id=f.id, sec_code=sec, company_name=hdr.get("company_name", hdr.get("iat_indicator", "")),
                          company_id=hdr.get("company_identification", hdr.get("originator_identification", "")),
                          company_entry_description=hdr.get("company_entry_description", ""),
                          transaction_code=e["transaction_code"], direction=direction, amount_cents=int(e["amount"]),
                          account_number=e["dfi_account_number"].strip(), individual_name=name,
                          individual_id=e.get("individual_id", ""), trace_number=e["trace_number"],
                          odfi_routing=e["trace_number"][:8] + str(_check_digit(e["trace_number"][:8])),
                          effective_date=from_yymmdd(hdr.get("effective_entry_date", "")), settlement_date=settle,
                          window_code=window, raw={**e, "batch_header": hdr})
        db.add(ie)
        db.flush()
        # Interbank settlement into suspense
        if ie.amount_cents:
            lines = ([("FRB_SETTLEMENT", ie.amount_cents, 0), ("ACH_RECEIVED_SUSPENSE", 0, ie.amount_cents)]
                     if direction == "credit" else
                     [("ACH_RECEIVED_SUSPENSE", ie.amount_cents, 0), ("FRB_SETTLEMENT", 0, ie.amount_cents)])
            ledger.post(db, f"inbound:{ie.id}", "inbound_settlement", f"Inbound {sec} {direction} {ie.trace_number}",
                        lines, file_id=f.id)
        _decision(db, ie, sec, e, now, s.rdfi_timezone)
        n += 1
    return n


def _check_digit(first8: str) -> int:
    from ..nacha.file import routing_check_digit
    return routing_check_digit(first8)


def _queue_return(db: Session, ie: InboundEntry, code: str, info: str = "") -> ReturnRecord:
    rc = RL.RETURN_CODES[code]
    r = ReturnRecord(direction="sent", inbound_entry_id=ie.id, reason_code=code, category=rc.category.value,
                     original_trace=ie.trace_number, amount_cents=ie.amount_cents, status="pending",
                     addenda_information=info[:44])
    db.add(r)
    ie.status, ie.return_code = "return_pending", code
    return r


def _decision(db: Session, ie: InboundEntry, sec: str, e: dict, now: datetime, tz: str) -> None:
    acct = core_banking.find_account(db, ie.account_number)
    if acct is None:
        _queue_return(db, ie, "R03")
        return
    ie.core_account_id = acct.id
    if acct.status == "closed":
        _queue_return(db, ie, "R02")
        return
    if acct.status == "frozen":
        _queue_return(db, ie, "R16")
        return
    if ie.direction == "debit":
        if acct.owner_type == "business" and (acct.debit_block or (
                acct.allowed_debit_company_ids and ie.company_id not in acct.allowed_debit_company_ids)):
            _queue_return(db, ie, "R29", "DEBIT BLOCK/FILTER")
            return
        if acct.account_type not in ("checking", "savings"):
            _queue_return(db, ie, "R20")
            return

    # Sanctions screening for IAT (full party data is carried in the addenda)
    if sec == "IAT":
        a = {x["addenda_type"]: x for x in e["addenda"]}
        parties = [("IAT receiver", a.get("10", {}).get("receiver_name", "")),
                   ("IAT originator", a.get("11", {}).get("originator_name", ""))]
        countries = [a.get("12", {}).get("originator_country_postal", "")[:2]]
        outcome, score, reasons = screening.sanctions_screen(db, [p for p in parties if p[1]], countries)
        screening.record(db, "sanctions", outcome, score, reasons, inbound_entry_id=ie.id)
        if outcome != "clear":
            ie.status = "held"
            db.add(Alert(kind="sanctions", severity="critical" if outcome == "block" else "high",
                         title=f"Inbound IAT sanctions screening hit ({ie.trace_number})",
                         inbound_entry_id=ie.id, details={"reasons": reasons}))
            return

    if ie.amount_cents == 0:          # prenote
        ie.status = "posted"
        return

    if ie.direction == "debit":
        if acct.available_balance_cents < ie.amount_cents:
            _queue_return(db, ie, "R01")
            return
        core_banking.post(db, acct, -ie.amount_cents, f"in:{ie.id}", f"ACH debit {ie.company_name} {ie.company_entry_description}")
        ledger.post(db, f"post:{ie.id}", "inbound_posting", f"Posted debit {ie.trace_number}",
                    [(f"CUST:{acct.id}", ie.amount_cents, 0), ("ACH_RECEIVED_SUSPENSE", 0, ie.amount_cents)])
        ie.status = "posted"
    else:
        score, reasons = screening.credit_monitoring(db, ie, acct, now)
        ie.risk_score = score
        s = get_settings()
        suspicious = score >= s.fraud_hold_score
        screening.record(db, "credit_monitoring", "review" if suspicious else "clear", score, reasons, inbound_entry_id=ie.id)
        avail = funds_availability(ie.window_code, ie.settlement_date, tz).astimezone(UTC)
        if suspicious:
            avail = now + timedelta(days=365)       # funds availability delayed pending review (Nacha 2026 credit monitoring)
        core_banking.post(db, acct, ie.amount_cents, f"in:{ie.id}", f"ACH credit {ie.company_name} {ie.company_entry_description}",
                          available_at=avail)
        ledger.post(db, f"post:{ie.id}", "inbound_posting", f"Posted credit {ie.trace_number}",
                    [("ACH_RECEIVED_SUSPENSE", ie.amount_cents, 0), (f"CUST:{acct.id}", 0, ie.amount_cents)])
        ie.funds_available_at = None if suspicious else avail
        ie.status = "held" if suspicious else "posted"
        if suspicious:
            db.add(Alert(kind="credit_monitoring", severity="high",
                         title=f"Suspicious inbound credit to {acct.owner_name}: ${ie.amount_cents / 100:,.2f}",
                         inbound_entry_id=ie.id, details={"score": score, "reasons": reasons}))

    # Auto-NOC when the transaction code does not match the account type (C05)
    want = "3" if acct.account_type == "savings" else "2"
    if ie.transaction_code[:1] in ("2", "3") and ie.transaction_code[:1] != want:
        corrected = want + ie.transaction_code[1:]
        db.add(NocRecord(direction="sent", inbound_entry_id=ie.id, change_code="C05", corrected_data=corrected,
                         original_trace=ie.trace_number, status="pending"))


def decide_inbound(db: Session, ie: InboundEntry, decision: str, actor: str, note: str = "", now: datetime | None = None) -> InboundEntry:
    now = now or utcnow()
    if ie.status != "held":
        raise DomainError("not_held", "Entry is not held", 409)
    posting = db.scalar(select(CorePosting).where(CorePosting.ref == f"in:{ie.id}"))
    if decision == "release":
        if posting is None:   # held before posting (e.g. sanctions) -> post now
            acct = db.get(CoreAccount, ie.core_account_id)
            sign = 1 if ie.direction == "credit" else -1
            core_banking.post(db, acct, sign * ie.amount_cents, f"in:{ie.id}", f"ACH {ie.direction} {ie.company_name}")
            lines = ([("ACH_RECEIVED_SUSPENSE", ie.amount_cents, 0), (f"CUST:{acct.id}", 0, ie.amount_cents)]
                     if ie.direction == "credit" else
                     [(f"CUST:{acct.id}", ie.amount_cents, 0), ("ACH_RECEIVED_SUSPENSE", 0, ie.amount_cents)])
            ledger.post(db, f"post:{ie.id}", "inbound_posting", f"Posted {ie.trace_number}", lines)
        else:
            posting.available_at = now
            core_banking.release_due_holds(db, now)
        ie.status, ie.funds_available_at = "posted", now
    elif decision == "return":
        if posting is not None and ie.direction == "credit":
            acct = db.get(CoreAccount, ie.core_account_id)
            if not posting.released:      # make the held credit visible, then reverse it (append-only)
                posting.released, posting.available_at = True, now
                acct.available_balance_cents += posting.amount_cents
            core_banking.post(db, acct, -ie.amount_cents, f"in:{ie.id}:reverse", f"Reverse held ACH credit {ie.trace_number}")
            ledger.post(db, f"unpost:{ie.id}", "inbound_unposting", f"Reversed posting {ie.trace_number}",
                        [(f"CUST:{acct.id}", ie.amount_cents, 0), ("ACH_RECEIVED_SUSPENSE", 0, ie.amount_cents)])
        _queue_return(db, ie, "R17" if ie.direction == "credit" else "R16", "QUESTIONABLE" if ie.direction == "credit" else "")
    else:
        raise DomainError("bad_decision", "decision must be release or return")
    for a in db.scalars(select(Alert).where(Alert.inbound_entry_id == ie.id, Alert.status != "closed")):
        a.status, a.disposition, a.disposition_note, a.closed_at, a.assignee = "closed", decision, note, now, actor
    audit(db, actor, f"inbound.{decision}", "inbound_entry", ie.id, note=note)
    return ie


def claim_unauthorized(db: Session, ie: InboundEntry, code: str, actor: str, wsud_on_file: bool,
                       now: datetime | None = None) -> ReturnRecord:
    """Customer disputes a posted debit: re-credit and return within the Nacha window."""
    now = now or utcnow()
    if ie.direction != "debit" or ie.status != "posted":
        raise DomainError("not_disputable", "Only posted debits can be returned as unauthorized")
    acct = db.get(CoreAccount, ie.core_account_id)
    if code in CONSUMER_UNAUTHORIZED:
        if acct.owner_type != "consumer":
            raise DomainError("wrong_code", "R05/R07/R10/R11 are for consumer accounts; use R29 for business accounts")
        if not wsud_on_file:
            raise DomainError("wsud_required", "A Written Statement of Unauthorized Debit is required")
        deadline = ie.settlement_date + timedelta(days=60)
    elif code == "R29":
        if acct.owner_type != "business":
            raise DomainError("wrong_code", "R29 is for business (non-consumer) accounts")
        deadline = calendar(db).add_banking_days(ie.settlement_date, 2)
    else:
        raise DomainError("bad_code", "Use R05, R07, R10, R11 or R29")
    if now.astimezone(ET).date() > deadline:
        raise DomainError("return_window", f"Return window for {code} closed on {deadline}")
    core_banking.post(db, acct, ie.amount_cents, f"in:{ie.id}:recredit", f"Recredit unauthorized ACH debit ({code})")
    ledger.post(db, f"recredit:{ie.id}", "recredit", f"Re-credit {code} {ie.trace_number}",
                [("ACH_RECEIVED_SUSPENSE", ie.amount_cents, 0), (f"CUST:{acct.id}", 0, ie.amount_cents)])
    r = _queue_return(db, ie, code, "WSUD ON FILE" if wsud_on_file else "")
    audit(db, actor, "inbound.unauthorized_claim", "inbound_entry", ie.id, code=code)
    return r


def create_noc(db: Session, ie: InboundEntry, code: str, corrected_data: str, actor: str) -> NocRecord:
    if code not in RL.NOC_CODES:
        raise DomainError("bad_noc_code", f"Unknown NOC code {code}")
    n = NocRecord(direction="sent", inbound_entry_id=ie.id, change_code=code, corrected_data=corrected_data[:29],
                  original_trace=ie.trace_number, status="pending")
    db.add(n)
    audit(db, actor, "noc.created", "inbound_entry", ie.id, code=code)
    return n


def pending_return_batches(db: Session) -> tuple[list[Batch], list[ReturnRecord], list[NocRecord]]:
    s = get_settings()
    rets = db.scalars(select(ReturnRecord).where(ReturnRecord.direction == "sent", ReturnRecord.status == "pending")).all()
    nocs = db.scalars(select(NocRecord).where(NocRecord.direction == "sent", NocRecord.status == "pending")).all()
    groups: dict[tuple, Batch] = {}
    today = utcnow().astimezone(ET).date()
    used_rets, used_nocs = [], []

    def batch_for(ie: InboundEntry, sec: str) -> Batch:
        hdr = ie.raw.get("batch_header", {})
        key = (sec, ie.company_id, ie.company_entry_description)
        if key not in groups:
            groups[key] = Batch(sec_code=sec, company_name=hdr.get("company_name", ie.company_name)[:16] or "RETURN",
                                company_id=ie.company_id, company_entry_description=ie.company_entry_description or "RETURN",
                                effective_entry_date=today, odfi_routing=s.bank_routing)
        return groups[key]

    for r in rets:
        ie = db.get(InboundEntry, r.inbound_entry_id)
        if ie.sec_code == "IAT":
            continue      # IAT returns are prepared manually in IAT format
        batch_for(ie, ie.sec_code).entries.append(
            return_entry(ie.raw, ie.odfi_routing, r.reason_code, s.bank_routing, r.addenda_information))
        used_rets.append(r)
    for n in nocs:
        ie = db.get(InboundEntry, n.inbound_entry_id)
        if ie.sec_code == "IAT":
            continue
        batch_for(ie, "COR").entries.append(noc_entry(ie.raw, ie.odfi_routing, n.change_code, n.corrected_data, s.bank_routing))
        used_nocs.append(n)
    return list(groups.values()), used_rets, used_nocs
