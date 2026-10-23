from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Payment
from ..services import sandbox as sim
from .deps import Principal, operator
from .schemas import SimInboundIn, SimNocIn, SimReturnIn

router = APIRouter(prefix="/v1/sandbox", tags=["sandbox (FedACH simulator)"])


def _guard():
    if not get_settings().is_sandbox:
        raise HTTPException(404, {"code": "not_found", "message": "Sandbox endpoints are disabled"})


def _pay(db: Session, pid: str) -> Payment:
    p = db.get(Payment, pid)
    if p is None:
        raise HTTPException(404, {"code": "not_found", "message": "Payment not found"})
    return p


@router.post("/returns", description="Simulate the RDFI returning one of our transmitted payments.")
def sim_return(body: SimReturnIn, p: Principal = Depends(operator), db: Session = Depends(get_db)):
    _guard()
    out = sim.simulate_return(db, _pay(db, body.payment_id), body.return_code, p.actor)
    db.commit()
    return out


@router.post("/nocs", description="Simulate the RDFI sending a Notification of Change.")
def sim_noc(body: SimNocIn, p: Principal = Depends(operator), db: Session = Depends(get_db)):
    _guard()
    out = sim.simulate_noc(db, _pay(db, body.payment_id), body.change_code, body.corrected_data, p.actor)
    db.commit()
    return out


@router.post("/inbound", description="Simulate FedACH delivering forward entries to our customers (RDFI flow).")
def sim_inbound(body: SimInboundIn, p: Principal = Depends(operator), db: Session = Depends(get_db)):
    _guard()
    out = sim.simulate_inbound(db, [e.model_dump() for e in body.entries], p.actor)
    db.commit()
    return out
