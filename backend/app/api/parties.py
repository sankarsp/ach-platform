from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..models import Counterparty, Originator, Payment
from ..services import originations as svc
from ..services.returns import return_rates
from .deps import Principal, operator, principal, scoped_originator
from .schemas import (AccountChangeIn, CounterpartyIn, CounterpartyOut, OriginatorIn, OriginatorOut,
                      OriginatorPatch)

router = APIRouter(prefix="/v1", tags=["originators & counterparties"])


@router.post("/originators", response_model=OriginatorOut, status_code=201)
def create_originator(body: OriginatorIn, p: Principal = Depends(operator), db: Session = Depends(get_db)):
    o = svc.create_originator(db, body.model_dump(), p.actor)
    db.commit()
    return o


@router.get("/originators")
def list_originators(p: Principal = Depends(principal), db: Session = Depends(get_db)):
    q = select(Originator).order_by(Originator.name)
    if not p.is_operator:
        q = q.where(Originator.id == p.originator_id)
    now = utcnow()
    out = []
    for o in db.scalars(q).all():
        d = OriginatorOut.model_validate(o).model_dump()
        d["return_rates"] = return_rates(db, o.id, now)
        d["payments_today"] = db.scalar(select(func.count(Payment.id)).where(
            Payment.originator_id == o.id, Payment.settlement_date == now.date())) or 0
        out.append(d)
    return out


@router.get("/originators/{originator_id}", response_model=OriginatorOut)
def get_originator(originator_id: str, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    return scoped_originator(db, p, originator_id)


@router.patch("/originators/{originator_id}", response_model=OriginatorOut)
def patch_originator(originator_id: str, body: OriginatorPatch, p: Principal = Depends(operator),
                     db: Session = Depends(get_db)):
    o = scoped_originator(db, p, originator_id)
    changes = body.model_dump(exclude_none=True)
    status = changes.pop("status", None)
    for k, v in changes.items():
        setattr(o, k, v)
    if status == "active":
        svc.activate_originator(db, o, p.actor)
    elif status == "suspended":
        o.status = "suspended"
    svc.audit(db, p.actor, "originator.updated", "originator", o.id, **{k: str(v) for k, v in body.model_dump(exclude_none=True).items()})
    db.commit()
    return o


@router.get("/originators/{originator_id}/return-rates")
def originator_return_rates(originator_id: str, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    o = scoped_originator(db, p, originator_id)
    return return_rates(db, o.id, utcnow())


@router.post("/counterparties", response_model=CounterpartyOut, status_code=201)
def create_counterparty(body: CounterpartyIn, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    org = scoped_originator(db, p, body.originator_id)
    data = body.model_dump(exclude={"originator_id", "validate_with"})
    cp = svc.create_counterparty(db, org, data, p.actor)
    if body.validate_with != "none":
        svc.validate_account(db, cp, body.validate_with, p.actor)
    db.commit()
    return cp


@router.get("/counterparties", response_model=list[CounterpartyOut])
def list_counterparties(originator_id: str | None = None, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    q = select(Counterparty).order_by(Counterparty.name)
    if not p.is_operator:
        q = q.where(Counterparty.originator_id == p.originator_id)
    elif originator_id:
        q = q.where(Counterparty.originator_id == originator_id)
    return db.scalars(q).all()


def _cp(db: Session, p: Principal, cid: str) -> Counterparty:
    cp = db.get(Counterparty, cid)
    if cp is None or (not p.is_operator and cp.originator_id != p.originator_id):
        raise HTTPException(404, {"code": "not_found", "message": "Counterparty not found"})
    return cp


@router.get("/counterparties/{counterparty_id}", response_model=CounterpartyOut)
def get_counterparty(counterparty_id: str, p: Principal = Depends(principal), db: Session = Depends(get_db)):
    return _cp(db, p, counterparty_id)


@router.put("/counterparties/{counterparty_id}/account", response_model=CounterpartyOut,
            description="Change bank details. Triggers re-validation and the 10-day 'changed account' fraud signal.")
def change_account(counterparty_id: str, body: AccountChangeIn, p: Principal = Depends(principal),
                   db: Session = Depends(get_db)):
    cp = svc.update_counterparty_account(db, _cp(db, p, counterparty_id), body.routing_number, body.account_number, p.actor)
    db.commit()
    return cp


@router.post("/counterparties/{counterparty_id}/validate", response_model=CounterpartyOut)
def validate(counterparty_id: str, method: str = "instant", p: Principal = Depends(principal), db: Session = Depends(get_db)):
    cp = svc.validate_account(db, _cp(db, p, counterparty_id), method, p.actor)
    db.commit()
    return cp
