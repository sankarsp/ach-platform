"""Webhook outbox delivery with HMAC signatures and exponential backoff."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..models import WebhookEvent, WebhookSubscription
from .common import sign_webhook


def deliver_pending(db: Session, now: datetime | None = None, client: httpx.Client | None = None, limit: int = 200) -> dict:
    now = now or utcnow()
    max_attempts = get_settings().webhook_max_attempts
    events = db.scalars(select(WebhookEvent).where(WebhookEvent.status == "pending", WebhookEvent.next_attempt_at <= now)
                        .order_by(WebhookEvent.created_at).limit(limit)).all()
    own = client is None
    client = client or httpx.Client(timeout=5)
    sent = failed = 0
    try:
        for ev in events:
            sub = db.get(WebhookSubscription, ev.subscription_id)
            if sub is None or not sub.active:
                ev.status = "stored"
                continue
            body = json.dumps({"id": ev.id, **ev.payload}, separators=(",", ":")).encode()
            ts = int(time.time())
            try:
                r = client.post(sub.url, content=body, headers={
                    "Content-Type": "application/json", "ACH-Event": ev.event_type, "ACH-Event-Id": ev.id,
                    "ACH-Signature": sign_webhook(sub.secret, body, ts)})
                ok = 200 <= r.status_code < 300
                err = None if ok else f"HTTP {r.status_code}"
            except Exception as exc:
                ok, err = False, str(exc)[:500]
            ev.attempts += 1
            if ok:
                ev.status, ev.last_error = "delivered", None
                sent += 1
            else:
                ev.last_error = err
                if ev.attempts >= max_attempts:
                    ev.status = "failed"
                    failed += 1
                else:
                    ev.next_attempt_at = now + timedelta(seconds=30 * 2 ** ev.attempts)
    finally:
        if own:
            client.close()
    return {"delivered": sent, "failed": failed, "attempted": len(events)}
