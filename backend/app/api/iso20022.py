"""ISO 20022 channel: pain.001 / pain.008 in, pain.002 / camt.054 / camt.053 out."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..domain.windows import ET
from ..iso20022 import intake, reports
from ..iso20022.samples import SAMPLES
from ..models import IsoMessage, Originator
from .deps import Principal, principal, scoped_originator

router = APIRouter(prefix="/v1/iso20022", tags=["ISO 20022"])
XML = "application/xml"


async def _body(request: Request) -> str:
    ctype = request.headers.get("content-type", "")
    raw = await request.body()
    if "json" in ctype:
        import json
        try:
            return json.loads(raw)["content"]
        except Exception:
            raise HTTPException(422, {"code": "bad_body", "message": 'Send XML, or JSON {"content": "<xml>"}'})
    return raw.decode("utf-8", errors="strict")


def _summary(m: IsoMessage) -> dict:
    counts: dict[str, int] = {}
    for r in m.results or []:
        counts[r.get("status", "")] = counts.get(r.get("status", ""), 0) + 1
    return {"id": m.id, "direction": m.direction, "msg_type": m.msg_type, "msg_id": m.msg_id,
            "originator_id": m.originator_id, "related_id": m.related_id, "status": m.status,
            "status_reason": m.status_reason, "nb_of_txs": m.nb_of_txs, "ctrl_sum_cents": m.ctrl_sum_cents,
            "created_at": m.created_at, "tx_status_counts": counts}


def _get(db: Session, p: Principal, mid: str) -> IsoMessage:
    m = db.get(IsoMessage, mid)
    if m is None or (not p.is_operator and m.originator_id != p.originator_id):
        raise HTTPException(404, {"code": "not_found", "message": "Message not found"})
    return m


def _ingest(request_xml: str, originator_id: str | None, p: Principal, db: Session, family: str):
    scoped = scoped_originator(db, p, originator_id) if (not p.is_operator or originator_id) else None
    m = intake.ingest(db, request_xml, scoped, p.actor, expect=family)
    status_report = reports.pain002(db, m)
    db.commit()
    return {"message": _summary(m), "results": m.results, "pain002_id": status_report.id}


@router.post("/pain.001", status_code=201,
             description="Submit a pain.001 (customer credit transfer initiation). Body: raw XML (application/xml) or "
                         'JSON {"content": "<xml>"}. Each transaction becomes an ACH credit through the standard controls; '
                         "a pain.002 status report is produced immediately.")
async def pain001(request: Request, originator_id: str | None = None, p: Principal = Depends(principal),
                  db: Session = Depends(get_db)):
    return _ingest(await _body(request), originator_id, p, db, "pain.001")


@router.post("/pain.008", status_code=201,
             description="Submit a pain.008 (customer direct debit initiation). Each transaction becomes an ACH debit; "
                         "a mandate (MndtId and DtOfSgntr) is required as authorization evidence.")
async def pain008(request: Request, originator_id: str | None = None, p: Principal = Depends(principal),
                  db: Session = Depends(get_db)):
    return _ingest(await _body(request), originator_id, p, db, "pain.008")


@router.get("/messages")
def messages(direction: str | None = None, limit: int = 100, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    q = select(IsoMessage).order_by(IsoMessage.created_at.desc()).limit(limit)
    if not p.is_operator:
        q = q.where(IsoMessage.originator_id == p.originator_id)
    if direction:
        q = q.where(IsoMessage.direction == direction)
    return [_summary(m) for m in db.scalars(q)]


@router.get("/messages/{message_id}")
def message(message_id: str, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    m = _get(db, p, message_id)
    out = {**_summary(m), "results": []}
    for r in m.results or []:
        st, code, info, pay = reports.tx_status(db, r) if m.direction == "inbound" else (r.get("status"), None, "", None)
        out["results"].append({**r, "current_status": st, "current_reason": code, "current_info": info,
                               "payment_status": pay.status if pay else None})
    return out


@router.get("/messages/{message_id}/xml", response_class=Response)
def message_xml(message_id: str, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    m = _get(db, p, message_id)
    return Response(m.content, media_type=XML,
                    headers={"Content-Disposition": f'attachment; filename="{m.msg_type}-{m.msg_id}.xml"'})


@router.get("/messages/{message_id}/pain.002", response_class=Response,
            description="Current payment status report for a received pain.001/pain.008 (reflects settlement and returns).")
def status_report(message_id: str, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    m = _get(db, p, message_id)
    out = reports.pain002(db, m)
    db.commit()
    return Response(out.content, media_type=XML,
                    headers={"Content-Disposition": f'attachment; filename="pain.002-{out.msg_id}.xml"'})


def _org_and_day(db: Session, p: Principal, originator_id: str | None, day: date | None) -> tuple[Originator, date]:
    return scoped_originator(db, p, originator_id), day or utcnow().astimezone(ET).date()


@router.get("/camt.054", response_class=Response,
            description="Debit/credit notification for a booking day: settled entries and returns (with ISO and Nacha "
                        "reason codes); NOCs in AddtlNtfctnInf.")
def camt054(originator_id: str | None = None, day: date | None = None, p: Principal = Depends(principal),
            db: Session = Depends(get_db)):
    org, d = _org_and_day(db, p, originator_id, day)
    out = reports.camt054(db, org, d)
    db.commit()
    return Response(out.content, media_type=XML, headers={"Content-Disposition": f'attachment; filename="camt.054-{out.msg_id}.xml"'})


@router.get("/camt.053", response_class=Response,
            description="End-of-day statement of the originator's settlement account (opening/closing balance and entries).")
def camt053(originator_id: str | None = None, day: date | None = None, p: Principal = Depends(principal),
            db: Session = Depends(get_db)):
    org, d = _org_and_day(db, p, originator_id, day)
    out = reports.camt053(db, org, d)
    db.commit()
    return Response(out.content, media_type=XML, headers={"Content-Disposition": f'attachment; filename="camt.053-{out.msg_id}.xml"'})


@router.get("/samples")
def samples(_: Principal = Depends(principal)):
    return [{"name": k, "description": v[0]} for k, v in SAMPLES.items()]


@router.get("/samples/{name}", response_class=Response)
def sample(name: str, _: Principal = Depends(principal)):
    if name not in SAMPLES:
        raise HTTPException(404, {"code": "not_found", "message": "Unknown sample"})
    return Response(SAMPLES[name][1](), media_type=XML)
