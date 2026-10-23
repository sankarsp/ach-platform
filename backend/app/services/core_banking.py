"""Core banking adapter.

In production this calls the bank's core (Fiserv, Jack Henry, FIS, Temenos…).
Here it is a simulated core with the same contract: look up accounts, post
debits/credits with an availability time, and report postings for
reconciliation.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import CoreAccount, CorePosting


def find_account(db: Session, account_number: str) -> CoreAccount | None:
    return db.scalar(select(CoreAccount).where(CoreAccount.account_number == account_number.strip()))


def post(db: Session, acct: CoreAccount, amount_cents: int, ref: str, description: str,
         available_at: datetime | None = None) -> CorePosting | None:
    """Signed amount: positive credits the customer. Idempotent by ref."""
    if db.scalar(select(CorePosting.id).where(CorePosting.ref == ref)):
        return None
    now = utcnow()
    available_at = available_at or now
    released = available_at <= now or amount_cents < 0
    p = CorePosting(core_account_id=acct.id, amount_cents=amount_cents, ref=ref, description=description,
                    available_at=available_at, released=released)
    acct.ledger_balance_cents += amount_cents
    if released:
        acct.available_balance_cents += amount_cents
    db.add(p)
    db.flush()
    return p


def release_due_holds(db: Session, now: datetime | None = None) -> int:
    now = now or utcnow()
    rows = db.scalars(select(CorePosting).where(CorePosting.released.is_(False), CorePosting.available_at <= now)).all()
    for p in rows:
        acct = db.get(CoreAccount, p.core_account_id)
        acct.available_balance_cents += p.amount_cents
        p.released = True
    return len(rows)


def net_for_refs(db: Session, prefix: str) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(CorePosting.amount_cents), 0))
                         .where(CorePosting.ref.like(prefix + "%"))) or 0)
