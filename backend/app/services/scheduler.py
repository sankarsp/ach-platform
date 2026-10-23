"""Operations heartbeat: release at cut-off, settle, release holds, monitor, deliver webhooks."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from ..config import get_settings
from ..db import SessionLocal, utcnow
from . import core_banking, monitoring, release, webhooks

log = logging.getLogger("ach.scheduler")


def tick(now: datetime | None = None, force: bool = False, actor: str = "scheduler") -> dict:
    now = now or utcnow()
    out: dict = {"at": now.isoformat()}
    db = SessionLocal()
    try:
        files = release.release_due(db, now, force=force, actor=actor)
        out["files_released"] = [{"id": f.id, "kind": f.kind, "window": f.window_code, "entries": f.entry_count} for f in files]
        db.commit()
        out["settled"] = release.settle_due(db, now, force=force, actor=actor)
        db.commit()
        out["holds_released"] = core_banking.release_due_holds(db, now)
        out["incidents"] = monitoring.run_monitors(db, now)
        db.commit()
        out["webhooks"] = webhooks.deliver_pending(db, now)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("scheduler tick failed")
        raise
    finally:
        db.close()
    return out


async def run_forever(stop: asyncio.Event) -> None:
    interval = get_settings().scheduler_interval_seconds
    while not stop.is_set():
        try:
            await asyncio.to_thread(tick)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
