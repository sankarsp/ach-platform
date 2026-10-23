"""Sandbox-only FedACH simulator: returns, NOCs and inbound forward files.

Lets testers exercise the ODFI return/NOC paths and the RDFI receipt path
without a FedACH test environment.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..domain import rules as RL
from ..domain.windows import ET
from ..models import Counterparty, Payment
from ..nacha.builders import noc_entry, return_entry
from ..nacha.file import Batch, Entry, NachaFile
from .common import DomainError, detokenize
from .returns import ingest_inbound_file

OTHER_ODFI = "026009593"


def _original(db: Session, p: Payment) -> tuple[dict, Counterparty]:
    if not p.trace_number:
        raise DomainError("not_transmitted", "Payment has not been transmitted yet", 409)
    cp = db.get(Counterparty, p.counterparty_id)
    return {"transaction_code": p.transaction_code, "dfi_account_number": detokenize(db, cp.account_token),
            "amount": str(p.amount_cents), "individual_id": cp.individual_id or p.id[:15],
            "individual_name": cp.name[:22], "trace_number": p.trace_number}, cp


def _file(batches: list[Batch], now: datetime) -> str:
    s = get_settings()
    return NachaFile(immediate_destination=s.bank_routing, immediate_origin=s.fed_routing, destination_name=s.bank_name,
                     origin_name=s.fed_name, created_at=now.astimezone(ET), batches=batches).render()


def simulate_return(db: Session, p: Payment, code: str, actor: str, now: datetime | None = None) -> dict:
    now = now or utcnow()
    if code not in RL.RETURN_CODES:
        raise DomainError("bad_code", f"Unknown return code {code}")
    orig, cp = _original(db, p)
    s = get_settings()
    rtn = cp.routing_number if cp.country == "US" else OTHER_ODFI
    b = Batch(sec_code=p.sec_code if p.sec_code != "IAT" else "CCD", company_name=p.originator.name[:16],
              company_id=p.originator.company_id, company_entry_description=p.company_entry_description,
              effective_entry_date=now.astimezone(ET).date(), odfi_routing=rtn,
              entries=[return_entry(orig, s.bank_routing, code, rtn)])
    return ingest_inbound_file(db, _file([b], now), actor, now)


def simulate_noc(db: Session, p: Payment, code: str, corrected_data: str, actor: str, now: datetime | None = None) -> dict:
    now = now or utcnow()
    if code not in RL.NOC_CODES:
        raise DomainError("bad_code", f"Unknown NOC code {code}")
    orig, cp = _original(db, p)
    s = get_settings()
    b = Batch(sec_code="COR", company_name=p.originator.name[:16], company_id=p.originator.company_id,
              company_entry_description=p.company_entry_description, effective_entry_date=now.astimezone(ET).date(),
              odfi_routing=cp.routing_number, entries=[noc_entry(orig, s.bank_routing, code, corrected_data, cp.routing_number)])
    return ingest_inbound_file(db, _file([b], now), actor, now)


def simulate_inbound(db: Session, items: list[dict], actor: str, now: datetime | None = None) -> dict:
    """items: account_number, amount_cents, direction, sec_code, company_name, company_id,
    individual_name, entry_description, same_day_window (SD1/SD2/SD3 or None), account_type."""
    now = now or utcnow()
    s = get_settings()
    batches: dict[tuple, Batch] = {}
    for it in items:
        sec = it.get("sec_code", "PPD")
        sd = it.get("same_day_window")
        key = (sec, it.get("company_id", "9876543210"), it.get("entry_description", "PAYMENT"), sd)
        if key not in batches:
            batches[key] = Batch(sec_code=sec, company_name=it.get("company_name", "Outside Co")[:16],
                                 company_id=key[1], company_entry_description=key[2],
                                 effective_entry_date=now.astimezone(ET).date(), odfi_routing=OTHER_ODFI,
                                 company_descriptive_date={"SD1": "SD1300", "SD2": "SD1700", "SD3": "SD1800"}.get(sd, ""))
        acct_type = it.get("account_type", "checking")
        txn = RL.TRANSACTION_CODES[(acct_type, it.get("direction", "credit"))]
        batches[key].entries.append(Entry(txn, s.bank_routing, it["account_number"], int(it["amount_cents"]),
                                          it.get("individual_id", ""), it.get("individual_name", "")))
    return ingest_inbound_file(db, _file(list(batches.values()), now), actor, now)
