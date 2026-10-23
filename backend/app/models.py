"""Persistence model. Money is always integer cents; times are UTC."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (JSON, BigInteger, Boolean, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, UTCDateTime, utcnow


def uid() -> str:
    return uuid.uuid4().hex


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


# ------------------------------------------------------------------ access
class ApiClient(Base, TimestampMixin):
    __tablename__ = "api_clients"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    key_prefix: Mapped[str] = mapped_column(String(12))
    role: Mapped[str] = mapped_column(String(20))           # originator | operator | admin
    originator_id: Mapped[str | None] = mapped_column(ForeignKey("originators.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_keys"
    client_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ------------------------------------------------------------------ parties
class Originator(Base, TimestampMixin):
    __tablename__ = "originators"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    company_id: Mapped[str] = mapped_column(String(10), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")   # pending | active | suspended
    kyb_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    risk_rating: Mapped[str] = mapped_column(String(10), default="medium")
    is_third_party_sender: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_sec_codes: Mapped[list] = mapped_column(JSON, default=lambda: ["PPD", "CCD"])
    allows_debits: Mapped[bool] = mapped_column(Boolean, default=False)
    same_day_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_limit_cents: Mapped[int] = mapped_column(BigInteger, default=1_000_000_00)
    per_entry_limit_cents: Mapped[int] = mapped_column(BigInteger, default=250_000_00)
    settlement_account_number: Mapped[str | None] = mapped_column(String(34), nullable=True)
    agreement_signed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    address: Mapped[dict] = mapped_column(JSON, default=dict)


class Counterparty(Base, TimestampMixin):
    __tablename__ = "counterparties"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    originator_id: Mapped[str] = mapped_column(ForeignKey("originators.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    party_type: Mapped[str] = mapped_column(String(10), default="consumer")  # consumer | business
    routing_number: Mapped[str] = mapped_column(String(34))
    account_token: Mapped[str] = mapped_column(String(64), index=True)
    account_last4: Mapped[str] = mapped_column(String(4))
    account_type: Mapped[str] = mapped_column(String(10), default="checking")
    individual_id: Mapped[str] = mapped_column(String(15), default="")
    country: Mapped[str] = mapped_column(String(2), default="US")
    address: Mapped[dict] = mapped_column(JSON, default=dict)
    validation_status: Mapped[str] = mapped_column(String(20), default="unverified")  # unverified|verified|failed
    validation_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")       # active | blocked
    block_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    account_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    first_paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class VaultSecret(Base):
    """Tokenized account numbers (Fernet-encrypted at field level)."""
    __tablename__ = "vault"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ------------------------------------------------------------------ payments
class Payment(Base, TimestampMixin):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("originator_id", "idempotency_key"),
                      Index("ix_payments_status_window", "status", "window_cutoff_at"))
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    originator_id: Mapped[str] = mapped_column(ForeignKey("originators.id"), index=True)
    counterparty_id: Mapped[str] = mapped_column(ForeignKey("counterparties.id"), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    direction: Mapped[str] = mapped_column(String(6))                     # credit | debit
    sec_code: Mapped[str] = mapped_column(String(3))
    purpose: Mapped[str] = mapped_column(String(30), default="other")
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    company_entry_description: Mapped[str] = mapped_column(String(10))
    transaction_code: Mapped[str] = mapped_column(String(2))
    requested_effective_date: Mapped[date] = mapped_column(Date)
    effective_date: Mapped[date] = mapped_column(Date)
    same_day_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    same_day: Mapped[bool] = mapped_column(Boolean, default=False)
    window_code: Mapped[str | None] = mapped_column(String(4), nullable=True)
    window_cutoff_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    fed_deadline_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    expected_settlement_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="received", index=True)
    status_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plan_notes: Mapped[list] = mapped_column(JSON, default=list)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    trace_number: Mapped[str | None] = mapped_column(String(15), index=True, nullable=True)
    file_id: Mapped[str | None] = mapped_column(ForeignKey("ach_files.id"), nullable=True)
    addenda: Mapped[list] = mapped_column(JSON, default=list)
    web_payment_type: Mapped[str | None] = mapped_column(String(1), nullable=True)
    iat: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    return_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    reversal_of_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    transmitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    returned_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    originator: Mapped[Originator] = relationship()
    counterparty: Mapped[Counterparty] = relationship()
    events: Mapped[list["PaymentEvent"]] = relationship(back_populates="payment", order_by="PaymentEvent.id")


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id"), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(String(60), default="system")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    payment: Mapped[Payment] = relationship(back_populates="events")


class ScreeningResult(Base):
    __tablename__ = "screening_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"), index=True, nullable=True)
    inbound_entry_id: Mapped[str | None] = mapped_column(ForeignKey("inbound_entries.id"), index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(20))       # sanctions | fraud | aml | credit_monitoring
    outcome: Mapped[str] = mapped_column(String(10))    # clear | review | block
    score: Mapped[int] = mapped_column(Integer, default=0)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SanctionsEntry(Base):
    __tablename__ = "sanctions_list"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    list_name: Mapped[str] = mapped_column(String(20), default="SDN")
    program: Mapped[str] = mapped_column(String(60), default="")
    country: Mapped[str] = mapped_column(String(2), default="")


# ------------------------------------------------------------------ alerts & cases
class Alert(Base, TimestampMixin):
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    severity: Mapped[str] = mapped_column(String(10), default="medium")
    status: Mapped[str] = mapped_column(String(15), default="open", index=True)  # open | escalated | closed
    title: Mapped[str] = mapped_column(String(200))
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"), nullable=True, index=True)
    inbound_entry_id: Mapped[str | None] = mapped_column(ForeignKey("inbound_entries.id"), nullable=True)
    originator_id: Mapped[str | None] = mapped_column(ForeignKey("originators.id"), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    disposition: Mapped[str | None] = mapped_column(String(20), nullable=True)  # approved | rejected | no_action
    disposition_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignee: Mapped[str | None] = mapped_column(String(60), nullable=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id"), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Case(Base, TimestampMixin):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | sar_filed | closed_no_action
    subject: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[list] = mapped_column(JSON, default=list)
    sar_reference: Mapped[str | None] = mapped_column(String(40), nullable=True)
    alerts: Mapped[list[Alert]] = relationship()


# ------------------------------------------------------------------ files
class AchFile(Base):
    __tablename__ = "ach_files"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    direction: Mapped[str] = mapped_column(String(10))           # outbound | inbound
    kind: Mapped[str] = mapped_column(String(15), default="forward")  # forward | returns | mixed
    window_code: Mapped[str | None] = mapped_column(String(4), nullable=True)
    file_id_modifier: Mapped[str] = mapped_column(String(1), default="A")
    status: Mapped[str] = mapped_column(String(15), default="built")  # built|transmitted|acknowledged|settled|rejected|processed
    content: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    entry_count: Mapped[int] = mapped_column(Integer, default=0)
    total_debit_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    total_credit_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    settlement_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    transmitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    errors: Mapped[list] = mapped_column(JSON, default=list)


# ------------------------------------------------------------------ ledger
class LedgerAccount(Base):
    __tablename__ = "ledger_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(10))  # asset | liability | income | expense


class LedgerTransaction(Base):
    __tablename__ = "ledger_transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ref: Mapped[str] = mapped_column(String(120), unique=True)   # idempotent posting key
    kind: Mapped[str] = mapped_column(String(30))
    description: Mapped[str] = mapped_column(String(255))
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"), nullable=True, index=True)
    file_id: Mapped[str | None] = mapped_column(ForeignKey("ach_files.id"), nullable=True, index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    postings: Mapped[list["LedgerPosting"]] = relationship(back_populates="txn")


class LedgerPosting(Base):
    __tablename__ = "ledger_postings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    txn_id: Mapped[int] = mapped_column(ForeignKey("ledger_transactions.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("ledger_accounts.id"), index=True)
    debit_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    credit_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    txn: Mapped[LedgerTransaction] = relationship(back_populates="postings")
    account: Mapped[LedgerAccount] = relationship()


# ------------------------------------------------------------------ simulated core banking
class CoreAccount(Base, TimestampMixin):
    __tablename__ = "core_accounts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    account_number: Mapped[str] = mapped_column(String(34), unique=True)
    owner_name: Mapped[str] = mapped_column(String(120))
    owner_type: Mapped[str] = mapped_column(String(10), default="consumer")
    account_type: Mapped[str] = mapped_column(String(10), default="checking")
    status: Mapped[str] = mapped_column(String(10), default="open")   # open | closed | frozen
    ledger_balance_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    available_balance_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    debit_block: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_debit_company_ids: Mapped[list] = mapped_column(JSON, default=list)  # debit filter
    opened_on: Mapped[date] = mapped_column(Date, default=date.today)


class CorePosting(Base):
    __tablename__ = "core_postings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    core_account_id: Mapped[str] = mapped_column(ForeignKey("core_accounts.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)       # signed
    ref: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(String(255))
    available_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    released: Mapped[bool] = mapped_column(Boolean, default=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ------------------------------------------------------------------ RDFI
class InboundEntry(Base, TimestampMixin):
    __tablename__ = "inbound_entries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    file_id: Mapped[str] = mapped_column(ForeignKey("ach_files.id"), index=True)
    sec_code: Mapped[str] = mapped_column(String(3))
    company_name: Mapped[str] = mapped_column(String(40))
    company_id: Mapped[str] = mapped_column(String(10))
    company_entry_description: Mapped[str] = mapped_column(String(10), default="")
    transaction_code: Mapped[str] = mapped_column(String(2))
    direction: Mapped[str] = mapped_column(String(6))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    account_number: Mapped[str] = mapped_column(String(35))
    individual_name: Mapped[str] = mapped_column(String(35), default="")
    individual_id: Mapped[str] = mapped_column(String(15), default="")
    trace_number: Mapped[str] = mapped_column(String(15), index=True)
    odfi_routing: Mapped[str] = mapped_column(String(9))
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    settlement_date: Mapped[date] = mapped_column(Date)
    window_code: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(15), default="received")  # posted | held | returned | return_pending
    core_account_id: Mapped[str | None] = mapped_column(ForeignKey("core_accounts.id"), nullable=True)
    funds_available_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    return_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class ReturnRecord(Base):
    __tablename__ = "returns"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    direction: Mapped[str] = mapped_column(String(10))      # received (ODFI side) | sent (RDFI side)
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"), nullable=True, index=True)
    inbound_entry_id: Mapped[str | None] = mapped_column(ForeignKey("inbound_entries.id"), nullable=True)
    originator_id: Mapped[str | None] = mapped_column(ForeignKey("originators.id"), nullable=True, index=True)
    reason_code: Mapped[str] = mapped_column(String(3))
    category: Mapped[str] = mapped_column(String(15))
    original_trace: Mapped[str] = mapped_column(String(15))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    timely: Mapped[bool] = mapped_column(Boolean, default=True)
    file_id: Mapped[str | None] = mapped_column(ForeignKey("ach_files.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(15), default="processed")  # pending | processed | sent
    addenda_information: Mapped[str] = mapped_column(String(44), default="")
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class NocRecord(Base):
    __tablename__ = "nocs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    direction: Mapped[str] = mapped_column(String(10), default="received")
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    inbound_entry_id: Mapped[str | None] = mapped_column(ForeignKey("inbound_entries.id"), nullable=True)
    counterparty_id: Mapped[str | None] = mapped_column(ForeignKey("counterparties.id"), nullable=True)
    originator_id: Mapped[str | None] = mapped_column(ForeignKey("originators.id"), nullable=True)
    change_code: Mapped[str] = mapped_column(String(3))
    corrected_data: Mapped[str] = mapped_column(String(29))
    original_trace: Mapped[str] = mapped_column(String(15))
    apply_by: Mapped[date | None] = mapped_column(Date, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(15), default="received")  # received | applied | pending | sent
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ------------------------------------------------------------------ operations
class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True)
    priority: Mapped[str] = mapped_column(String(2))          # P1..P4
    category: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(15), default="open")  # open | acknowledged | resolved
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    external_ref: Mapped[str | None] = mapped_column(String(60), nullable=True)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    file_id: Mapped[str] = mapped_column(ForeignKey("ach_files.id"), index=True)
    settlement_date: Mapped[date] = mapped_column(Date)
    window_code: Mapped[str | None] = mapped_column(String(4), nullable=True)
    advice_net_cents: Mapped[int] = mapped_column(BigInteger)
    ledger_net_cents: Mapped[int] = mapped_column(BigInteger)
    core_net_cents: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(10))     # matched | break
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class WebhookSubscription(Base, TimestampMixin):
    __tablename__ = "webhook_subscriptions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    originator_id: Mapped[str | None] = mapped_column(ForeignKey("originators.id"), nullable=True)
    url: Mapped[str] = mapped_column(String(500))
    secret: Mapped[str] = mapped_column(String(64))
    events: Mapped[list] = mapped_column(JSON, default=lambda: ["*"])
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    subscription_id: Mapped[str | None] = mapped_column(ForeignKey("webhook_subscriptions.id"), nullable=True)
    originator_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | delivered | failed | stored
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CalendarOverride(Base):
    __tablename__ = "calendar_overrides"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))      # closure | opening
    note: Mapped[str] = mapped_column(String(200))
    requested_by: Mapped[str] = mapped_column(String(60))
    approved_by: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class Counter(Base):
    __tablename__ = "counters"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, default=0)
