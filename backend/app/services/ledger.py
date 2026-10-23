"""Append-only double-entry ledger.

Chart of accounts
  FRB_SETTLEMENT            asset      Fed master account
  ACH_CLEARING_OUT          liability  credits released to FedACH, not yet settled
  ACH_RECEIVED_SUSPENSE     liability  inbound entries not yet posted to a customer
  ORIG:<originator_id>      liability  originator settlement (deposit) account
  CUST:<core_account_id>    liability  RDFI customer deposit account
  FEE_INCOME                income
Postings are keyed by ``ref`` so every posting is idempotent.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import LedgerAccount, LedgerPosting, LedgerTransaction
from .common import DomainError

SYSTEM_ACCOUNTS = {
    "FRB_SETTLEMENT": ("Federal Reserve settlement account", "asset"),
    "ACH_CLEARING_OUT": ("Outbound ACH clearing", "liability"),
    "ACH_RECEIVED_SUSPENSE": ("Inbound ACH suspense", "liability"),
    "FEE_INCOME": ("ACH fee income", "income"),
}


def account(db: Session, code: str, name: str | None = None, type_: str = "liability") -> LedgerAccount:
    acc = db.scalar(select(LedgerAccount).where(LedgerAccount.code == code))
    if acc is None:
        n, t = SYSTEM_ACCOUNTS.get(code, (name or code, type_))
        acc = LedgerAccount(code=code, name=n, type=t)
        db.add(acc)
        db.flush()
    return acc


def post(db: Session, ref: str, kind: str, description: str, lines: list[tuple[str, int, int]],
         payment_id: str | None = None, file_id: str | None = None) -> LedgerTransaction | None:
    """lines: (account_code, debit_cents, credit_cents). Returns None if already posted."""
    if db.scalar(select(LedgerTransaction.id).where(LedgerTransaction.ref == ref)):
        return None
    debits = sum(d for _, d, _ in lines)
    credits = sum(c for _, _, c in lines)
    if debits != credits:
        raise DomainError("ledger_unbalanced", f"Unbalanced posting {ref}: {debits} != {credits}", 500)
    if any(d < 0 or c < 0 for _, d, c in lines):
        raise DomainError("ledger_negative", "Posting amounts must be non-negative", 500)
    txn = LedgerTransaction(ref=ref, kind=kind, description=description, payment_id=payment_id, file_id=file_id)
    db.add(txn)
    db.flush()
    for code, d, c in lines:
        if d == 0 and c == 0:
            continue
        db.add(LedgerPosting(txn_id=txn.id, account_id=account(db, code).id, debit_cents=d, credit_cents=c))
    db.flush()
    return txn


def balance(db: Session, code: str) -> int:
    """Natural-sign balance: assets debit-positive, liabilities/income credit-positive."""
    acc = db.scalar(select(LedgerAccount).where(LedgerAccount.code == code))
    if acc is None:
        return 0
    d, c = db.execute(select(func.coalesce(func.sum(LedgerPosting.debit_cents), 0),
                             func.coalesce(func.sum(LedgerPosting.credit_cents), 0))
                      .where(LedgerPosting.account_id == acc.id)).one()
    return int(d - c) if acc.type in ("asset", "expense") else int(c - d)


def trial_balance(db: Session) -> dict:
    rows = db.execute(select(LedgerAccount.code, LedgerAccount.name, LedgerAccount.type,
                             func.coalesce(func.sum(LedgerPosting.debit_cents), 0),
                             func.coalesce(func.sum(LedgerPosting.credit_cents), 0))
                      .join(LedgerPosting, LedgerPosting.account_id == LedgerAccount.id, isouter=True)
                      .group_by(LedgerAccount.id).order_by(LedgerAccount.code)).all()
    accounts = [{"code": r[0], "name": r[1], "type": r[2], "debits": int(r[3]), "credits": int(r[4]),
                 "balance": int(r[3] - r[4]) if r[2] in ("asset", "expense") else int(r[4] - r[3])} for r in rows]
    td, tc = sum(a["debits"] for a in accounts), sum(a["credits"] for a in accounts)
    return {"accounts": accounts, "total_debits": td, "total_credits": tc, "balanced": td == tc}


def file_frb_net(db: Session, file_id: str) -> int:
    """Net movement of the Fed account for a file (debits - credits)."""
    acc = db.scalar(select(LedgerAccount).where(LedgerAccount.code == "FRB_SETTLEMENT"))
    if acc is None:
        return 0
    d, c = db.execute(select(func.coalesce(func.sum(LedgerPosting.debit_cents), 0),
                             func.coalesce(func.sum(LedgerPosting.credit_cents), 0))
                      .join(LedgerTransaction, LedgerTransaction.id == LedgerPosting.txn_id)
                      .where(LedgerPosting.account_id == acc.id, LedgerTransaction.file_id == file_id)).one()
    return int(d - c)
