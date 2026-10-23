from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..models import AuditLog, Payment, PaymentEvent, VaultSecret, WebhookEvent, WebhookSubscription


class DomainError(Exception):
    """Business-rule violation; surfaces as HTTP 422 with a machine-readable code."""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# ------------------------------------------------------------------ audit
def audit(db: Session, actor: str, action: str, entity: str, entity_id: str, **detail) -> None:
    db.add(AuditLog(actor=actor, action=action, entity=entity, entity_id=str(entity_id), detail=_json(detail)))


def _json(d):
    return json.loads(json.dumps(d, default=str))


# ------------------------------------------------------------------ payment state machine
TRANSITIONS = {
    "received": {"held", "warehoused", "rejected"},
    "held": {"warehoused", "rejected", "cancelled"},
    "warehoused": {"transmitted", "cancelled", "held"},
    "transmitted": {"settled", "returned"},
    "settled": {"returned", "reversed"},
    "returned": set(),
    "rejected": set(),
    "cancelled": set(),
    "reversed": {"returned"},
}

EVENT_FOR_STATUS = {
    "held": "payment.held", "warehoused": "payment.approved", "rejected": "payment.rejected",
    "cancelled": "payment.cancelled", "transmitted": "payment.transmitted", "settled": "payment.settled",
    "returned": "payment.returned", "reversed": "payment.reversed",
}


def set_status(db: Session, p: Payment, to: str, actor: str = "system", reason: str | None = None, **detail) -> None:
    frm = p.status
    if to != frm and to not in TRANSITIONS.get(frm, set()):
        raise DomainError("invalid_transition", f"Payment {p.id} cannot move from {frm} to {to}", 409)
    p.status = to
    if reason is not None:
        p.status_reason = reason
    db.add(PaymentEvent(payment_id=p.id, from_status=frm, to_status=to, actor=actor,
                        detail=_json({"reason": reason, **detail} if reason else detail)))
    if to in EVENT_FOR_STATUS:
        emit(db, p.originator_id, EVENT_FOR_STATUS[to], payment_payload(p))


def payment_payload(p: Payment) -> dict:
    return {
        "id": p.id, "status": p.status, "status_reason": p.status_reason, "amount_cents": p.amount_cents,
        "direction": p.direction, "sec_code": p.sec_code, "effective_date": p.effective_date,
        "settlement_date": p.settlement_date, "window": p.window_code, "same_day": p.same_day,
        "trace_number": p.trace_number, "return_code": p.return_code,
        "expected_settlement_at": p.expected_settlement_at,
    }


# ------------------------------------------------------------------ webhooks outbox
def emit(db: Session, originator_id: str | None, event_type: str, data: dict) -> None:
    subs = db.scalars(select(WebhookSubscription).where(WebhookSubscription.active.is_(True))).all()
    targets = [s for s in subs if (s.originator_id in (None, originator_id)) and
               ("*" in s.events or event_type in s.events)]
    payload = _json({"type": event_type, "created_at": utcnow(), "data": data})
    if not targets:
        db.add(WebhookEvent(originator_id=originator_id, event_type=event_type, payload=payload, status="stored"))
    for s in targets:
        db.add(WebhookEvent(subscription_id=s.id, originator_id=originator_id, event_type=event_type, payload=payload))


def sign_webhook(secret: str, body: bytes, ts: int) -> str:
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


# ------------------------------------------------------------------ account vault
def _fernet() -> Fernet:
    return Fernet(get_settings().vault_key.encode())


def fingerprint(value: str) -> str:
    return hmac.new(get_settings().vault_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def tokenize(db: Session, account_number: str) -> str:
    fp = fingerprint(account_number)
    existing = db.scalar(select(VaultSecret).where(VaultSecret.fingerprint == fp))
    if existing:
        return existing.token
    token = "acct_" + secrets.token_urlsafe(18)
    db.add(VaultSecret(token=token, ciphertext=_fernet().encrypt(account_number.encode()).decode(), fingerprint=fp))
    db.flush()
    return token


def detokenize(db: Session, token: str) -> str:
    v = db.get(VaultSecret, token)
    if v is None:
        raise DomainError("vault_missing", "Account token not found", 500)
    return _fernet().decrypt(v.ciphertext.encode()).decode()


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_api_key(prefix: str = "ach") -> str:
    return f"{prefix}_{base64.urlsafe_b64encode(secrets.token_bytes(24)).decode().rstrip('=')}"


def as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=utcnow().tzinfo)
