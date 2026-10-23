"""ITSM: event monitoring, incident creation and notification.

Incidents are de-duplicated by key and pushed to the configured ITSM endpoint
(ServiceNow, Jira Service Management or PagerDuty Events API compatible JSON).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..domain.windows import ET, window_instances
from ..models import Alert, InboundEntry, Incident, Payment, WebhookEvent

log = logging.getLogger("ach.itsm")


def open_incident(db: Session, key: str, priority: str, category: str, title: str, details: dict) -> Incident:
    inc = db.scalar(select(Incident).where(Incident.dedupe_key == key))
    if inc:
        if inc.status == "resolved":
            return inc
        inc.details = {**inc.details, **{k: str(v) for k, v in details.items()}, "last_seen": utcnow().isoformat()}
        return inc
    inc = Incident(dedupe_key=key, priority=priority, category=category, title=title,
                   details={k: str(v) for k, v in details.items()})
    db.add(inc)
    db.flush()
    _notify(inc)
    return inc


def _notify(inc: Incident) -> None:
    url = get_settings().itsm_webhook_url
    if not url:
        return
    body = {"short_description": inc.title, "urgency": {"P1": 1, "P2": 2, "P3": 3}.get(inc.priority, 4),
            "category": inc.category, "correlation_id": inc.dedupe_key, "details": inc.details}
    try:
        r = httpx.post(url, json=body, timeout=5)
        inc.notified = r.status_code < 300
        inc.external_ref = r.json().get("result", {}).get("number") if r.headers.get("content-type", "").startswith("application/json") else None
    except Exception as exc:  # never let ITSM outages block payments
        log.warning("ITSM notify failed: %s", exc)


def update_incident(db: Session, inc: Incident, action: str, actor: str, note: str = "") -> Incident:
    now = utcnow()
    if action == "acknowledge":
        inc.status, inc.acknowledged_at = "acknowledged", now
    elif action == "resolve":
        inc.status, inc.resolved_at, inc.resolution = "resolved", now, f"{actor}: {note}"
    return inc


def run_monitors(db: Session, now: datetime | None = None) -> list[str]:
    """Checks run every scheduler tick. Returns the incident keys raised."""
    now = now or utcnow()
    raised: list[str] = []
    today = now.astimezone(ET).date()

    for w in window_instances(today, 1):
        cutoff = w.internal_cutoff
        mins = (cutoff - now).total_seconds() / 60
        held = db.scalar(select(func.count(Payment.id)).where(
            Payment.status == "held", Payment.window_cutoff_at == cutoff)) or 0
        if 0 < mins <= 30 and held:
            key = f"window_at_risk:{today}:{w.code}"
            open_incident(db, key, "P2", "window_at_risk",
                          f"{held} held payment(s) will miss {w.code} cut-off in {int(mins)} min unless reviewed",
                          {"window": w.code, "cutoff": cutoff.isoformat(), "held": held})
            raised.append(key)
        stuck = db.scalar(select(func.count(Payment.id)).where(
            Payment.status == "warehoused", Payment.fed_deadline_at == w.fed_deadline,
            Payment.fed_deadline_at < now)) or 0
        if stuck:
            key = f"window_missed:{today}:{w.code}"
            open_incident(db, key, "P1", "window_missed",
                          f"{stuck} payment(s) not released before the {w.code} Fed deadline",
                          {"window": w.code, "fed_deadline": w.fed_deadline.isoformat(), "payments": stuck})
            raised.append(key)

    stale = db.scalar(select(func.count(Alert.id)).where(
        Alert.status == "open", Alert.created_at < now - timedelta(hours=4))) or 0
    if stale:
        key = f"alert_sla:{today}"
        open_incident(db, key, "P3", "alert_sla", f"{stale} alert(s) open longer than 4 hours", {"open_alerts": stale})
        raised.append(key)

    failed = db.scalar(select(func.count(WebhookEvent.id)).where(WebhookEvent.status == "failed")) or 0
    if failed:
        key = f"webhooks_failed:{today}"
        open_incident(db, key, "P2", "webhook_failure", f"{failed} webhook event(s) failed after retries", {"failed": failed})
        raised.append(key)

    held_credits = db.scalar(select(func.count(InboundEntry.id)).where(
        InboundEntry.status == "held", InboundEntry.created_at < now - timedelta(hours=24))) or 0
    if held_credits:
        key = f"inbound_holds:{today}"
        open_incident(db, key, "P3", "inbound_hold_sla", f"{held_credits} inbound entr(ies) held over 24 hours",
                      {"held": held_credits})
        raised.append(key)
    return raised
