"""Origination: onboarding, counterparties and the payment intake pipeline.

intake -> validate -> plan window -> sanctions -> fraud -> AML -> held | warehoused
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..domain import rules as RL
from ..domain.windows import UTC, plan_settlement
from ..models import Alert, Counterparty, CoreAccount, Originator, Payment
from ..nacha.file import valid_routing
from . import screening
from .calendar_service import calendar
from .common import DomainError, audit, emit, set_status, tokenize

PURPOSES = {
    "payroll": {"sec": ["PPD", "CCD"], "direction": "credit"},
    "pension_benefits": {"sec": ["PPD"], "direction": "credit"},
    "refund": {"sec": ["PPD", "CCD", "WEB"], "direction": "credit"},
    "expense_reimbursement": {"sec": ["PPD", "CCD"], "direction": "credit"},
    "bill_payment": {"sec": ["PPD", "WEB", "TEL", "CCD"], "direction": "debit"},
    "loan_disbursement": {"sec": ["PPD", "CCD"], "direction": "credit"},
    "loan_repayment": {"sec": ["PPD", "WEB", "TEL", "CCD"], "direction": "debit"},
    "purchase": {"sec": ["WEB", "TEL", "PPD"], "direction": "debit"},
    "p2p": {"sec": ["WEB"], "direction": "credit"},
    "vendor": {"sec": ["CCD", "CTX", "IAT"], "direction": "credit"},
    "tax": {"sec": ["CCD"], "direction": None},
    "child_support": {"sec": ["CCD"], "direction": "credit"},
    "cash_concentration": {"sec": ["CCD"], "direction": None},
    "cross_border": {"sec": ["IAT"], "direction": None},
    "reversal": {"sec": list(RL.SEC_CODES), "direction": None},
    "prenote": {"sec": list(RL.SEC_CODES), "direction": None},
    "other": {"sec": list(RL.SEC_CODES), "direction": None},
}

USE_CASES = [
    {"use_case": "Salary / payroll", "purpose": "payroll", "direction": "credit", "sec": "PPD", "rule": "Company entry description PAYROLL (since Mar 20, 2026)"},
    {"use_case": "Pension, benefits, refunds", "purpose": "pension_benefits", "direction": "credit", "sec": "PPD", "rule": "CCD if the receiver is a business"},
    {"use_case": "Automatic bill payment", "purpose": "bill_payment", "direction": "debit", "sec": "PPD / WEB / TEL", "rule": "Reg E preauthorized transfer; stop payment 3 business days before"},
    {"use_case": "Loan disbursement", "purpose": "loan_disbursement", "direction": "credit", "sec": "PPD; CCD for business", "rule": "Same Day up to the per-entry limit"},
    {"use_case": "Loan repayment / EMI", "purpose": "loan_repayment", "direction": "debit", "sec": "PPD / WEB; CCD for business", "rule": "Autopay cannot be a condition of credit (Reg E)"},
    {"use_case": "Online purchase / bill pay", "purpose": "purchase", "direction": "debit", "sec": "WEB", "rule": "PURCHASE description; validate first-use accounts"},
    {"use_case": "Person-to-person transfer", "purpose": "p2p", "direction": "credit", "sec": "WEB", "rule": "Consumer-to-consumer, initiated online"},
    {"use_case": "Vendor / supplier payment", "purpose": "vendor", "direction": "credit", "sec": "CCD or CTX", "rule": "CTX when sending invoice detail (EDI 820)"},
    {"use_case": "Business tax (EFTPS)", "purpose": "tax", "direction": "credit or debit", "sec": "CCD + TXP addenda", "rule": "Standard tax payment format"},
    {"use_case": "Child support", "purpose": "child_support", "direction": "credit", "sec": "CCD + DED addenda", "rule": "Sent by employers with payroll"},
    {"use_case": "Cash concentration", "purpose": "cash_concentration", "direction": "credit or debit", "sec": "CCD", "rule": "Between a company's own accounts"},
    {"use_case": "Cross-border payment", "purpose": "cross_border", "direction": "credit or debit", "sec": "IAT", "rule": "Not Same Day eligible; full party data for OFAC"},
]


# ------------------------------------------------------------------ onboarding
def create_originator(db: Session, data: dict, actor: str) -> Originator:
    if db.scalar(select(Originator).where(Originator.company_id == data["company_id"])):
        raise DomainError("duplicate_company_id", "An originator with this company ID already exists", 409)
    bad = set(data.get("allowed_sec_codes", [])) - set(RL.SEC_CODES)
    if bad:
        raise DomainError("bad_sec_code", f"Unknown SEC codes: {sorted(bad)}")
    o = Originator(**data)
    db.add(o)
    db.flush()
    audit(db, actor, "originator.created", "originator", o.id, company_id=o.company_id)
    return o


def activate_originator(db: Session, o: Originator, actor: str) -> Originator:
    if o.kyb_status != "approved":
        raise DomainError("kyb_incomplete", "KYB must be approved before activation")
    if not o.agreement_signed_at:
        raise DomainError("agreement_missing", "Originator agreement binding the Nacha Rules must be on file")
    o.status = "active"
    audit(db, actor, "originator.activated", "originator", o.id)
    return o


def create_counterparty(db: Session, org: Originator, data: dict, actor: str) -> Counterparty:
    routing, account = data.pop("routing_number"), data.pop("account_number")
    country = data.get("country", "US")
    if country == "US" and not valid_routing(routing):
        raise DomainError("invalid_routing", "Routing number fails the ABA check digit")
    if not account.replace("-", "").isalnum() or len(account) > (35 if country != "US" else 17):
        raise DomainError("invalid_account", "Account number format is invalid")
    cp = Counterparty(originator_id=org.id, routing_number=routing, account_token=tokenize(db, account),
                      account_last4=account[-4:], **data)
    db.add(cp)
    db.flush()
    audit(db, actor, "counterparty.created", "counterparty", cp.id, originator_id=org.id)
    return cp


def update_counterparty_account(db: Session, cp: Counterparty, routing: str, account: str, actor: str) -> Counterparty:
    if cp.country == "US" and not valid_routing(routing):
        raise DomainError("invalid_routing", "Routing number fails the ABA check digit")
    cp.routing_number, cp.account_token, cp.account_last4 = routing, tokenize(db, account), account[-4:]
    cp.account_changed_at = utcnow()          # drives the payroll-diversion / vendor-impersonation fraud rule
    cp.validation_status = "unverified"
    cp.status, cp.block_reason = "active", None
    audit(db, actor, "counterparty.account_changed", "counterparty", cp.id)
    return cp


def validate_account(db: Session, cp: Counterparty, method: str, actor: str) -> Counterparty:
    """Account validation. 'instant' simulates a third-party verification service;
    'prenote' sends a zero-dollar prenotification entry."""
    if method == "instant":
        ok = cp.account_last4 != "0000" and (cp.country != "US" or valid_routing(cp.routing_number))
        cp.validation_status = "verified" if ok else "failed"
        cp.validation_method = "instant"
    elif method == "prenote":
        org = db.get(Originator, cp.originator_id)
        wanted = ["PPD", "WEB", "TEL"] if cp.party_type == "consumer" else ["CCD", "CTX"]
        sec = next((s for s in wanted if s in org.allowed_sec_codes and RL.SEC_CODES[s].credits), None)
        if sec is None or org.status != "active":
            raise DomainError("prenote_unavailable", "Originator cannot send a prenote for this receiver type")
        cp.validation_status = "prenote_sent"
        cp.validation_method = "prenote"
        db.flush()
        create_payment(db, org, PaymentRequest(counterparty_id=cp.id, amount_cents=0, direction="credit", sec_code=sec,
                                               effective_date=utcnow().date(), purpose="prenote", prenote=True), actor)
    else:
        raise DomainError("bad_method", "method must be instant or prenote")
    audit(db, actor, "counterparty.validation", "counterparty", cp.id, method=method, result=cp.validation_status)
    return cp


# ------------------------------------------------------------------ payments
@dataclass
class PaymentRequest:
    counterparty_id: str
    amount_cents: int
    direction: str
    sec_code: str
    effective_date: date
    purpose: str = "other"
    company_entry_description: str | None = None
    same_day: bool = False
    addenda: list[str] = field(default_factory=list)
    web_payment_type: str | None = None
    iat: dict | None = None
    idempotency_key: str | None = None
    metadata: dict = field(default_factory=dict)
    reversal_of_id: str | None = None
    prenote: bool = False


def _entry_description(req: PaymentRequest, on: date) -> str:
    std = RL.STANDARD_ENTRY_DESCRIPTIONS.at(on)
    given = (req.company_entry_description or "").strip().upper()
    if req.purpose == "reversal":
        return "REVERSAL"
    if req.prenote:
        return given or "PRENOTE"
    if std and req.purpose == "payroll" and req.sec_code == "PPD" and req.direction == "credit":
        if given and given != std["payroll"]:
            raise DomainError("entry_description", "PPD wage credits must use company entry description PAYROLL")
        return std["payroll"]
    if std and req.purpose == "purchase" and req.sec_code == "WEB" and req.direction == "debit":
        if given and given != std["purchase"]:
            raise DomainError("entry_description", "WEB debits for e-commerce purchases must use PURCHASE")
        return std["purchase"]
    if not given:
        raise DomainError("entry_description", "company_entry_description is required (max 10 characters)")
    if len(given) > 10:
        raise DomainError("entry_description", "company_entry_description is at most 10 characters")
    return given


def _validate(req: PaymentRequest, org: Originator, cp: Counterparty) -> None:
    sec = RL.SEC_CODES.get(req.sec_code)
    if sec is None:
        raise DomainError("bad_sec_code", f"Unknown SEC code {req.sec_code}")
    if req.sec_code not in org.allowed_sec_codes:
        raise DomainError("sec_not_allowed", f"Originator is not enabled for {req.sec_code}")
    if req.direction not in ("credit", "debit"):
        raise DomainError("bad_direction", "direction must be credit or debit")
    if req.direction == "credit" and not sec.credits:
        raise DomainError("sec_direction", f"{req.sec_code} does not allow credits")
    if req.direction == "debit" and not sec.debits:
        raise DomainError("sec_direction", f"{req.sec_code} does not allow debits")
    if req.direction == "debit" and not org.allows_debits:
        raise DomainError("debits_not_allowed", "Originator is not approved to originate debits")
    if req.purpose not in PURPOSES:
        raise DomainError("bad_purpose", f"Unknown purpose {req.purpose}")
    if req.amount_cents < 0 or (req.amount_cents == 0 and not req.prenote):
        raise DomainError("bad_amount", "amount_cents must be positive")
    if req.amount_cents > org.per_entry_limit_cents:
        raise DomainError("per_entry_limit", f"Amount exceeds the originator per-entry limit of ${org.per_entry_limit_cents / 100:,.2f}")
    if sec.receiver.value == "consumer" and cp.party_type != "consumer":
        raise DomainError("party_mismatch", f"{req.sec_code} is for consumer receivers; use CCD/CTX for businesses")
    if sec.receiver.value == "business" and cp.party_type != "business":
        raise DomainError("party_mismatch", f"{req.sec_code} is for business receivers; use PPD/WEB/TEL for consumers")
    if req.sec_code == "WEB" and req.direction == "credit" and req.purpose not in ("p2p", "refund", "other", "prenote"):
        raise DomainError("web_credit", "WEB credits are for person-to-person transfers")
    if len(req.addenda) > sec.max_addenda and req.sec_code != "IAT":
        raise DomainError("too_many_addenda", f"{req.sec_code} allows at most {sec.max_addenda} addenda")
    if any(len(a) > 80 for a in req.addenda):
        raise DomainError("addenda_length", "Each addenda is at most 80 characters")
    if req.sec_code == "IAT":
        need = {"transaction_type", "originator", "receiver", "rdfi", "destination_country"}
        missing = need - set((req.iat or {}).keys())
        if missing:
            raise DomainError("iat_fields", f"IAT requires {sorted(missing)}")
    elif cp.country != "US":
        raise DomainError("iat_required", "Payments to a non-US account must be sent as IAT")
    if cp.status != "active":
        raise DomainError("counterparty_blocked", f"Receiver account is blocked: {cp.block_reason or 'blocked'}")


def create_payment(db: Session, org: Originator, req: PaymentRequest, actor: str, now: datetime | None = None) -> Payment:
    now = now or utcnow()
    s = get_settings()
    if org.status != "active":
        raise DomainError("originator_inactive", "Originator is not active", 403)
    cp = db.get(Counterparty, req.counterparty_id)
    if cp is None or cp.originator_id != org.id:
        raise DomainError("counterparty_not_found", "Counterparty not found", 404)
    _validate(req, org, cp)
    desc = _entry_description(req, req.effective_date)
    cal = calendar(db)
    plan = plan_settlement(now, req.effective_date, req.sec_code, req.amount_cents,
                           same_day_requested=req.same_day and org.same_day_enabled, cal=cal)
    notes = list(plan.reasons)
    if req.same_day and not org.same_day_enabled:
        notes.append("Originator is not enabled for Same Day ACH; next-day settlement")

    account_type = cp.account_type if cp.account_type in ("checking", "savings", "gl", "loan") else "checking"
    txn = RL.TRANSACTION_CODES[(account_type, req.direction)]
    if req.prenote:
        txn = RL.PRENOTE_CODES[txn]

    p = Payment(originator_id=org.id, counterparty_id=cp.id, idempotency_key=req.idempotency_key,
                direction=req.direction, sec_code=req.sec_code, purpose=req.purpose, amount_cents=req.amount_cents,
                company_entry_description=desc, transaction_code=txn,
                requested_effective_date=req.effective_date, effective_date=plan.effective_date,
                same_day_requested=req.same_day, same_day=plan.same_day,
                window_code=plan.window.code, window_cutoff_at=plan.window.internal_cutoff.astimezone(UTC),
                fed_deadline_at=plan.window.fed_deadline.astimezone(UTC),
                expected_settlement_at=plan.window.settlement.astimezone(UTC),
                settlement_date=plan.window.settlement_date, plan_notes=notes, addenda=req.addenda,
                web_payment_type=(req.web_payment_type or ("S" if req.sec_code == "WEB" else None)),
                iat=req.iat, metadata_=req.metadata, reversal_of_id=req.reversal_of_id, status="received")
    db.add(p)
    db.flush()
    set_status(db, p, "received", actor, plan=plan.to_dict())

    holds: list[tuple[str, str, str, list[str]]] = []  # (kind, severity, title, reasons)

    # 1. Sanctions
    parties = [("Receiver", cp.name), ("Originator", org.name)]
    countries = [cp.country]
    if req.iat:
        parties += [("IAT receiver", req.iat.get("receiver", {}).get("name", "")),
                    ("IAT originator", req.iat.get("originator", {}).get("name", ""))]
        countries += [req.iat.get("destination_country", ""), req.iat.get("receiver", {}).get("country", "")]
    outcome, sscore, sreasons = screening.sanctions_screen(db, [x for x in parties if x[1]], countries)
    screening.record(db, "sanctions", outcome, sscore, sreasons, payment_id=p.id)
    if outcome != "clear":
        holds.append(("sanctions", "critical" if outcome == "block" else "high", "Potential sanctions match", sreasons))

    # 2. Fraud
    fscore, freasons = (0, ["Prenotification: no funds move"]) if req.prenote else screening.fraud_score(db, p, cp, org, now)
    foutcome = "block" if fscore >= s.fraud_block_score else ("review" if fscore >= s.fraud_hold_score else "clear")
    screening.record(db, "fraud", foutcome, fscore, freasons, payment_id=p.id)
    p.risk_score = fscore
    if foutcome != "clear":
        holds.append(("fraud", "critical" if foutcome == "block" else "high",
                      f"Fraud score {fscore} on {req.direction} to {cp.name}", freasons))

    # 3. AML (post-event monitoring; creates an alert but does not hold)
    aoutcome, areasons = screening.aml_checks(db, p, cp, now)
    screening.record(db, "aml", aoutcome, 0, areasons, payment_id=p.id)
    if aoutcome != "clear":
        db.add(Alert(kind="aml", severity="medium", title=f"AML typology on payment to {cp.name}",
                     payment_id=p.id, originator_id=org.id, details={"reasons": areasons}))

    # 4. Exposure limit (originator daily limit by settlement date)
    used = db.scalar(select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
        Payment.originator_id == org.id, Payment.settlement_date == p.settlement_date, Payment.id != p.id,
        Payment.status.not_in(["rejected", "cancelled", "returned"]))) or 0
    if used + p.amount_cents > org.daily_limit_cents:
        holds.append(("exposure_limit", "high", "Originator daily exposure limit exceeded",
                      [f"${(used + p.amount_cents) / 100:,.2f} would exceed the daily limit of ${org.daily_limit_cents / 100:,.2f}"]))

    if holds:
        for kind, sev, title, reasons in holds:
            db.add(Alert(kind=kind, severity=sev, title=title, payment_id=p.id, originator_id=org.id,
                         details={"reasons": reasons}))
        set_status(db, p, "held", actor, reason="; ".join(h[2] for h in holds))
    else:
        set_status(db, p, "warehoused", actor, reason="Passed pre-release controls")
    audit(db, actor, "payment.created", "payment", p.id, amount_cents=p.amount_cents, status=p.status)
    return p


def replan(db: Session, p: Payment, now: datetime) -> None:
    plan = plan_settlement(now, p.requested_effective_date, p.sec_code, p.amount_cents,
                           same_day_requested=p.same_day_requested and p.originator.same_day_enabled, cal=calendar(db))
    p.effective_date = plan.effective_date
    p.same_day = plan.same_day
    p.window_code = plan.window.code
    p.window_cutoff_at = plan.window.internal_cutoff.astimezone(UTC)
    p.fed_deadline_at = plan.window.fed_deadline.astimezone(UTC)
    p.expected_settlement_at = plan.window.settlement.astimezone(UTC)
    p.settlement_date = plan.window.settlement_date
    p.plan_notes = list(p.plan_notes or []) + [f"Re-planned at {now.isoformat()}: {plan.window.code} settles {plan.window.settlement_date}"] + plan.reasons


def decide_hold(db: Session, p: Payment, decision: str, actor: str, note: str = "", now: datetime | None = None) -> Payment:
    now = now or utcnow()
    if p.status != "held":
        raise DomainError("not_held", "Payment is not on hold", 409)
    alerts = db.scalars(select(Alert).where(Alert.payment_id == p.id, Alert.status != "closed")).all()
    if decision == "approve":
        if any(a.kind == "sanctions" and a.severity == "critical" for a in alerts) and "OFAC-CLEARED" not in note.upper():
            raise DomainError("sanctions_block", "Probable sanctions match: record 'OFAC-cleared' with the false-positive rationale to release", 409)
        replan(db, p, now)
        set_status(db, p, "warehoused", actor, reason=f"Released by analyst: {note}".strip())
    elif decision == "reject":
        set_status(db, p, "rejected", actor, reason=f"Rejected by analyst: {note}".strip())
    else:
        raise DomainError("bad_decision", "decision must be approve or reject")
    for a in alerts:
        a.status, a.disposition, a.disposition_note, a.closed_at, a.assignee = "closed", decision + "d", note, now, actor
    audit(db, actor, f"payment.hold_{decision}d", "payment", p.id, note=note)
    return p


def cancel_payment(db: Session, p: Payment, actor: str) -> Payment:
    if p.status not in ("held", "warehoused"):
        raise DomainError("not_cancellable", f"Payments in status {p.status} cannot be cancelled", 409)
    set_status(db, p, "cancelled", actor, reason="Cancelled before release")
    audit(db, actor, "payment.cancelled", "payment", p.id)
    return p


def create_reversal(db: Session, p: Payment, reason: str, actor: str, now: datetime | None = None) -> Payment:
    now = now or utcnow()
    if reason not in RL.REVERSAL_REASONS:
        raise DomainError("bad_reversal_reason", f"Reversals are only permitted for {sorted(RL.REVERSAL_REASONS)}")
    if p.status != "settled":
        raise DomainError("not_settled", "Only settled entries can be reversed", 409)
    cal = calendar(db)
    deadline = cal.add_banking_days(p.settlement_date, RL.REVERSAL_WINDOW_BANKING_DAYS)
    today = now.astimezone(UTC).date()
    if today > deadline:
        raise DomainError("reversal_window", f"Reversal window closed on {deadline} (5 banking days after settlement)")
    org = p.originator
    if p.direction == "credit" and not org.allows_debits:
        org_allows = False
    else:
        org_allows = True
    req = PaymentRequest(counterparty_id=p.counterparty_id, amount_cents=p.amount_cents,
                         direction="debit" if p.direction == "credit" else "credit", sec_code=p.sec_code,
                         effective_date=today, purpose="reversal", reversal_of_id=p.id,
                         metadata={"reversal_reason": reason})
    saved = org.allows_debits
    org.allows_debits = True if not org_allows else saved   # reversals are permitted even for credit-only originators
    try:
        rev = create_payment(db, org, req, actor, now)
    finally:
        org.allows_debits = saved
    set_status(db, p, "reversed", actor, reason=f"Reversal {rev.id} ({reason})")
    emit(db, org.id, "payment.reversal_created", {"original_id": p.id, "reversal_id": rev.id, "reason": reason})
    return rev


def originator_funding_account(db: Session, org: Originator) -> CoreAccount | None:
    if not org.settlement_account_number:
        return None
    return db.scalar(select(CoreAccount).where(CoreAccount.account_number == org.settlement_account_number))
