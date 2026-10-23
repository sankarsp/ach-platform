"""Pre-release controls: sanctions screening, fraud scoring, AML typologies and
RDFI credit monitoring.

These implement the documented, risk-based fraud monitoring the 2026 Nacha
rules require (false-pretenses fraud: BEC, vendor impersonation, payroll
diversion, account takeover). Every rule returns a reason so analysts and
examiners can see why an entry was held.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..domain.windows import ET
from ..models import (CoreAccount, Counterparty, InboundEntry, Originator, Payment, SanctionsEntry,
                      ScreeningResult)

SUFFIXES = {"INC", "LLC", "LTD", "CO", "CORP", "CORPORATION", "COMPANY", "LIMITED", "PLC", "SA", "GMBH", "THE"}


def normalize(name: str) -> str:
    tokens = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper()).split()
    return " ".join(t for t in tokens if t not in SUFFIXES)


def similarity(a: str, b: str) -> float:
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    direct = SequenceMatcher(None, a, b).ratio()
    sorted_ = SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio()
    return max(direct, sorted_)


# ------------------------------------------------------------------ sanctions
def sanctions_screen(db: Session, parties: list[tuple[str, str]], countries: list[str] | None = None) -> tuple[str, int, list[str]]:
    s = get_settings()
    entries = db.scalars(select(SanctionsEntry)).all()
    outcome, best, reasons = "clear", 0.0, []
    for role, name in parties:
        for e in entries:
            r = similarity(name, e.name)
            if r >= s.ofac_review_ratio:
                level = "block" if r >= s.ofac_block_ratio else "review"
                reasons.append(f"{role} '{name}' matches {e.list_name} entry '{e.name}' ({r:.0%})")
                best = max(best, r)
                if level == "block" or outcome == "clear":
                    outcome = level if outcome != "block" else outcome
    high_risk = set(s.high_risk_countries.split(","))
    for c in countries or []:
        if c and c.upper() in high_risk:
            reasons.append(f"Comprehensively sanctioned / high-risk jurisdiction: {c.upper()}")
            outcome = "block"
            best = max(best, 1.0)
    return outcome, int(best * 100), reasons


# ------------------------------------------------------------------ fraud (originating side)
def fraud_score(db: Session, p: Payment, cp: Counterparty, org: Originator, now: datetime) -> tuple[int, list[str]]:
    s = get_settings()
    score, reasons = 0, []

    def add(points: int, why: str):
        nonlocal score
        score += points
        reasons.append(f"+{points} {why}")

    first_use = cp.first_paid_at is None
    if first_use and p.amount_cents >= s.large_new_payee_cents:
        add(30, f"First payment to this account and amount ≥ ${s.large_new_payee_cents / 100:,.0f}")
    elif first_use:
        add(10, "First payment to this account")

    if cp.account_changed_at and now - cp.account_changed_at < timedelta(days=10):
        pts = 60 if p.direction == "credit" else 20   # credits: hold for call-back verification
        add(pts, "Receiver bank details changed in the last 10 days (payroll diversion / vendor impersonation pattern)")

    if p.sec_code == "WEB" and p.direction == "debit" and cp.validation_status != "verified":
        add(35, "WEB debit to an account without commercially reasonable account validation")

    if cp.validation_status == "failed":
        add(50, "Account previously failed validation or was returned R02/R03/R04")

    avg = db.scalar(select(func.avg(Payment.amount_cents)).where(
        Payment.originator_id == org.id, Payment.created_at >= now - timedelta(days=90),
        Payment.status.in_(["settled", "transmitted", "warehoused"]), Payment.id != p.id))
    if avg and p.amount_cents > 5 * float(avg) and p.amount_cents > 1_000_00:
        add(20, f"Amount is {p.amount_cents / float(avg):.1f}x the originator's 90-day average")

    recent = db.scalar(select(func.count(Payment.id)).where(
        Payment.originator_id == org.id, Payment.created_at >= now - timedelta(hours=1), Payment.id != p.id)) or 0
    if recent >= 50:
        add(15, f"Velocity: {recent} payments from this originator in the last hour")

    dup = db.scalar(select(func.count(Payment.id)).where(
        Payment.originator_id == org.id, Payment.counterparty_id == cp.id, Payment.amount_cents == p.amount_cents,
        Payment.direction == p.direction, Payment.created_at >= now - timedelta(hours=24), Payment.id != p.id,
        Payment.status.not_in(["rejected", "cancelled"])))
    if dup:
        add(40, "Possible duplicate: same receiver, amount and direction in the last 24 hours")

    if now.astimezone(ET).weekday() >= 5:
        add(5, "Submitted on a weekend")

    if org.risk_rating == "high":
        add(10, "Originator risk rating is high")
    if org.is_third_party_sender:
        add(5, "Third-Party Sender (nested originators)")
    return min(score, 100), reasons


# ------------------------------------------------------------------ AML typologies
def aml_checks(db: Session, p: Payment, cp: Counterparty, now: datetime) -> tuple[str, list[str]]:
    s = get_settings()
    reasons = []
    t = s.structuring_threshold_cents
    if int(t * 0.8) <= p.amount_cents < t:
        near = db.scalar(select(func.count(Payment.id)).where(
            Payment.counterparty_id == cp.id, Payment.amount_cents >= int(t * 0.8), Payment.amount_cents < t,
            Payment.created_at >= now - timedelta(days=3), Payment.id != p.id)) or 0
        if near >= 2:
            reasons.append(f"Structuring: {near + 1} payments just under ${t / 100:,.0f} to the same account in 3 days")
    if p.amount_cents >= 50_000_00 and p.amount_cents % 1_000_00 == 0 and p.sec_code == "IAT":
        reasons.append("Large round-dollar cross-border payment")
    return ("review" if reasons else "clear"), reasons


# ------------------------------------------------------------------ RDFI credit monitoring
def credit_monitoring(db: Session, e: InboundEntry, acct: CoreAccount, now: datetime) -> tuple[int, list[str]]:
    """Detect inbound credits received under false pretenses (money-mule / BEC proceeds)."""
    score, reasons = 0, []
    sim = similarity(e.individual_name, acct.owner_name)
    if e.individual_name and sim < 0.6:
        score += 35
        reasons.append(f"+35 Receiver name '{e.individual_name}' does not match account owner ({sim:.0%})")
    age = (now.date() - acct.opened_on).days
    if age < 30 and e.amount_cents >= 5_000_00:
        score += 30
        reasons.append(f"+30 Account opened {age} days ago receiving ${e.amount_cents / 100:,.2f}")
    others = db.scalar(select(func.count(func.distinct(InboundEntry.company_id))).where(
        InboundEntry.core_account_id == acct.id, InboundEntry.direction == "credit",
        InboundEntry.created_at >= now - timedelta(days=1), InboundEntry.id != e.id)) or 0
    if others >= 2:
        score += 25
        reasons.append(f"+25 Credits from {others + 1} different originators in 24 hours")
    if acct.owner_type == "consumer" and e.sec_code in ("CCD", "CTX") and e.amount_cents >= 25_000_00:
        score += 15
        reasons.append("+15 Large corporate credit into a consumer account")
    return min(score, 100), reasons


def record(db: Session, kind: str, outcome: str, score: int, reasons: list[str],
           payment_id: str | None = None, inbound_entry_id: str | None = None) -> ScreeningResult:
    r = ScreeningResult(kind=kind, outcome=outcome, score=score, reasons=reasons,
                        payment_id=payment_id, inbound_entry_id=inbound_entry_id)
    db.add(r)
    return r
