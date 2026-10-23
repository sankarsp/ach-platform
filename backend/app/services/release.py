"""Window release, transmission, settlement and three-way reconciliation."""
from __future__ import annotations

import hashlib
import string
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..domain.windows import ET, UTC
from ..models import (AchFile, Alert, Counter, Counterparty, InboundEntry, Originator, Payment,
                      ReconciliationRun)
from ..nacha.builders import iat_addenda
from ..nacha.file import Addenda, Batch, Entry, NachaFile
from . import core_banking, ledger
from .common import audit, detokenize, emit, set_status
from .monitoring import open_incident
from .originations import originator_funding_account, replan

MODIFIERS = string.ascii_uppercase + string.digits


def next_counter(db: Session, name: str) -> int:
    c = db.get(Counter, name, with_for_update=True)
    if c is None:
        c = Counter(name=name, value=0)
        db.add(c)
    c.value += 1
    db.flush()
    return c.value


def _file_id_modifier(db: Session, now: datetime) -> str:
    day_start = datetime.combine(now.astimezone(ET).date(), datetime.min.time(), tzinfo=ET).astimezone(UTC)
    n = db.scalar(select(func.count(AchFile.id)).where(AchFile.direction == "outbound", AchFile.created_at >= day_start)) or 0
    return MODIFIERS[n % len(MODIFIERS)]


def _entry_for(db: Session, p: Payment, cp: Counterparty, org: Originator) -> Entry:
    account = detokenize(db, cp.account_token)
    e = Entry(transaction_code=p.transaction_code, rdfi_routing=cp.routing_number if p.sec_code != "IAT" else p.iat["rdfi"].get("gateway_routing", cp.routing_number),
              account_number=account, amount_cents=p.amount_cents, individual_id=cp.individual_id or p.id[:15],
              individual_name=cp.name[:22] if p.sec_code != "CTX" else cp.name[:16],
              discretionary_data=(p.web_payment_type or "") if p.sec_code == "WEB" else "")
    if p.sec_code == "IAT":
        i = p.iat
        e.gateway_ofac_indicator = "0"
        e.addenda = iat_addenda(transaction_type=i["transaction_type"], amount_cents=p.amount_cents,
                                receiver_name=i["receiver"].get("name", cp.name),
                                originator={"name": org.name, **org.address, **i.get("originator", {})},
                                odfi={"name": get_settings().bank_name, "id": get_settings().bank_routing},
                                rdfi=i["rdfi"], receiver={**i["receiver"]}, remittance=p.addenda)
    else:
        e.addenda = [Addenda("05", {"payment_related_information": a}) for a in p.addenda]
    return e


def release_due(db: Session, now: datetime | None = None, force: bool = False, actor: str = "scheduler") -> list[AchFile]:
    """Release every warehoused payment whose internal cut-off has arrived.

    One file per (window, settlement date). ``force`` (sandbox only) releases
    everything warehoused immediately into its planned window.
    """
    now = now or utcnow()
    q = select(Payment).where(Payment.status == "warehoused")
    if not force:
        q = q.where(Payment.window_cutoff_at <= now)
    due = db.scalars(q.order_by(Payment.created_at)).all()

    # Payments whose Fed deadline already passed (e.g. platform outage) are re-planned, not sent late.
    ready: list[Payment] = []
    for p in due:
        if not force and p.fed_deadline_at and p.fed_deadline_at < now:
            replan(db, p, now)
            open_incident(db, f"missed:{p.window_code}:{p.id}", "P1", "window_missed",
                          f"Payment {p.id} missed its window and was re-planned",
                          {"payment_id": p.id, "new_window": p.window_code, "settlement_date": str(p.settlement_date)})
            if p.window_cutoff_at > now:
                continue
        ready.append(p)

    groups: dict[tuple, list[Payment]] = defaultdict(list)
    for p in ready:
        groups[(p.window_code, p.settlement_date, p.expected_settlement_at)].append(p)

    files = []
    for (window_code, settle_date, settle_at), payments in sorted(groups.items(), key=lambda g: g[0][2]):
        f = _build_and_transmit(db, window_code, settle_date, settle_at, payments, now, actor)
        if f:
            files.append(f)
    pending_returns = build_return_file(db, now, actor)
    if pending_returns:
        files.append(pending_returns)
    return files


def _build_and_transmit(db, window_code, settle_date, settle_at, payments, now, actor) -> AchFile | None:
    s = get_settings()
    # Prefunding check for credit origination: originator settlement account must cover credits.
    by_org: dict[str, list[Payment]] = defaultdict(list)
    for p in payments:
        by_org[p.originator_id].append(p)
    releasable: list[Payment] = []
    for org_id, ps in by_org.items():
        org = db.get(Originator, org_id)
        acct = originator_funding_account(db, org)
        credits = sum(p.amount_cents for p in ps if p.direction == "credit")
        if acct is not None and credits > acct.available_balance_cents:
            for p in ps:
                if p.direction == "credit":
                    set_status(db, p, "held", actor, reason="Insufficient prefunded balance in originator settlement account")
                    db.add(Alert(kind="prefunding", severity="high", title=f"Prefunding shortfall for {org.name}",
                                 payment_id=p.id, originator_id=org.id,
                                 details={"required_cents": credits, "available_cents": acct.available_balance_cents}))
                else:
                    releasable.append(p)
        else:
            releasable.extend(ps)
    if not releasable:
        return None

    batches: dict[tuple, Batch] = {}
    org_cache: dict[str, Originator] = {}
    for p in releasable:
        org = org_cache.setdefault(p.originator_id, db.get(Originator, p.originator_id))
        key = (org.id, p.sec_code, p.company_entry_description, p.settlement_date, p.same_day,
               (p.iat or {}).get("destination_country"))
        if key not in batches:
            desc_date = ""
            if p.same_day and p.expected_settlement_at:
                desc_date = "SD" + p.expected_settlement_at.astimezone(ET).strftime("%H%M")
            batches[key] = Batch(sec_code=p.sec_code, company_name=org.name[:16], company_id=org.company_id,
                                 company_entry_description=p.company_entry_description, effective_entry_date=p.settlement_date,
                                 odfi_routing=s.bank_routing, company_descriptive_date=desc_date,
                                 iat={"iso_destination_country": (p.iat or {}).get("destination_country", "US"),
                                      "originator_identification": org.company_id,
                                      "iso_destination_currency": (p.iat or {}).get("destination_currency", "USD")} if p.sec_code == "IAT" else {})
        cp = db.get(Counterparty, p.counterparty_id)
        entry = _entry_for(db, p, cp, org)
        entry.trace_number = f"{s.bank_routing[:8]}{next_counter(db, 'trace') % 10_000_000:07d}"
        p.trace_number = entry.trace_number
        batches[key].entries.append(entry)

    nf = NachaFile(immediate_destination=s.fed_routing, immediate_origin=s.bank_routing, destination_name=s.fed_name,
                   origin_name=s.bank_name, created_at=now.astimezone(ET), file_id_modifier=_file_id_modifier(db, now),
                   batches=list(batches.values()))
    for i, b in enumerate(nf.batches, start=1):
        b.batch_number = i
    content = nf.render()
    sha = hashlib.sha256(content.encode()).hexdigest()
    debit = sum(p.amount_cents for p in releasable if p.direction == "debit")
    credit = sum(p.amount_cents for p in releasable if p.direction == "credit")
    f = AchFile(direction="outbound", kind="forward", window_code=window_code, file_id_modifier=nf.file_id_modifier,
                content=content, sha256=sha, entry_count=len(releasable), total_debit_cents=debit,
                total_credit_cents=credit, settlement_date=settle_date, settlement_at=settle_at, status="built")
    db.add(f)
    db.flush()

    for p in releasable:
        org = org_cache[p.originator_id]
        p.file_id = f.id
        p.transmitted_at = now
        if p.direction == "credit" and p.amount_cents:
            ledger.post(db, f"release:{p.id}", "credit_release", f"Credit released {p.id}",
                        [(f"ORIG:{org.id}", p.amount_cents, 0), ("ACH_CLEARING_OUT", 0, p.amount_cents)],
                        payment_id=p.id, file_id=f.id)
            acct = originator_funding_account(db, org)
            if acct:
                core_banking.post(db, acct, -p.amount_cents, f"pay:{p.id}:release", f"ACH credit {p.trace_number}")
        set_status(db, p, "transmitted", actor, file_id=f.id, trace_number=p.trace_number)

    # Transmission to FedACH (FedLine / sponsor bank adapter). Sandbox: immediate acknowledgement.
    f.status, f.transmitted_at = "transmitted", now
    f.status, f.acknowledged_at = "acknowledged", now
    audit(db, actor, "file.transmitted", "ach_file", f.id, window=window_code, entries=len(releasable),
          debit=debit, credit=credit)
    emit(db, None, "file.transmitted", {"file_id": f.id, "window": window_code, "entries": len(releasable)})
    return f


def build_return_file(db: Session, now: datetime, actor: str) -> AchFile | None:
    """RDFI side: send queued returns and NOCs back to the ODFIs."""
    from .rdfi import pending_return_batches   # local import to avoid a cycle
    s = get_settings()
    batches, rets, nocs = pending_return_batches(db)
    if not batches:
        return None
    for i, b in enumerate(batches, start=1):
        b.batch_number = i
        for e in b.entries:
            seq_base = next_counter(db, "trace")
            e.trace_number = f"{s.bank_routing[:8]}{seq_base % 10_000_000:07d}"
    nf = NachaFile(immediate_destination=s.fed_routing, immediate_origin=s.bank_routing, destination_name=s.fed_name,
                   origin_name=s.bank_name, created_at=now.astimezone(ET), file_id_modifier=_file_id_modifier(db, now),
                   batches=batches)
    content = nf.render()
    f = AchFile(direction="outbound", kind="returns", window_code=None, file_id_modifier=nf.file_id_modifier,
                content=content, sha256=hashlib.sha256(content.encode()).hexdigest(),
                entry_count=sum(len(b.entries) for b in batches),
                total_debit_cents=sum(e.amount_cents for b in batches for e in b.entries if not e.is_credit),
                total_credit_cents=sum(e.amount_cents for b in batches for e in b.entries if e.is_credit),
                status="acknowledged", transmitted_at=now, acknowledged_at=now, settlement_date=now.astimezone(ET).date(),
                settlement_at=now)
    db.add(f)
    db.flush()
    for r in rets:
        r.status, r.file_id = "sent", f.id
        ie = db.get(InboundEntry, r.inbound_entry_id)
        # Returning a credit sends funds back out; returning a debit brings funds back in.
        lines = ([("ACH_RECEIVED_SUSPENSE", r.amount_cents, 0), ("FRB_SETTLEMENT", 0, r.amount_cents)]
                 if ie.direction == "credit" else
                 [("FRB_SETTLEMENT", r.amount_cents, 0), ("ACH_RECEIVED_SUSPENSE", 0, r.amount_cents)])
        ledger.post(db, f"return_sent:{r.id}", "return_sent", f"Return {r.reason_code} sent for {r.original_trace}",
                    lines, file_id=f.id)
    for n in nocs:
        n.status = "sent"
    audit(db, actor, "file.returns_transmitted", "ach_file", f.id, returns=len(rets), nocs=len(nocs))
    return f


def settle_due(db: Session, now: datetime | None = None, force: bool = False, actor: str = "scheduler") -> list[dict]:
    """Post settlement for acknowledged outbound forward files and reconcile three ways."""
    now = now or utcnow()
    q = select(AchFile).where(AchFile.direction == "outbound", AchFile.kind == "forward",
                              AchFile.status == "acknowledged")
    if not force:
        q = q.where(AchFile.settlement_at <= now)
    results = []
    for f in db.scalars(q).all():
        payments = db.scalars(select(Payment).where(Payment.file_id == f.id)).all()
        for p in payments:
            if p.status != "transmitted":
                continue
            settle_payment(db, p, f, now, actor)
        f.status, f.settled_at = "settled", now
        results.append(reconcile(db, f))
    return results


def settle_payment(db: Session, p: Payment, f: AchFile, now: datetime, actor: str) -> None:
    org = p.originator
    if p.amount_cents:
        if p.direction == "credit":
            ledger.post(db, f"settle:{p.id}", "credit_settlement", f"Credit settled {p.trace_number}",
                        [("ACH_CLEARING_OUT", p.amount_cents, 0), ("FRB_SETTLEMENT", 0, p.amount_cents)],
                        payment_id=p.id, file_id=f.id)
        else:
            ledger.post(db, f"settle:{p.id}", "debit_settlement", f"Debit settled {p.trace_number}",
                        [("FRB_SETTLEMENT", p.amount_cents, 0), (f"ORIG:{org.id}", 0, p.amount_cents)],
                        payment_id=p.id, file_id=f.id)
            acct = originator_funding_account(db, org)
            if acct:
                core_banking.post(db, acct, p.amount_cents, f"pay:{p.id}:settle", f"ACH debit collected {p.trace_number}")
    p.settled_at = now
    set_status(db, p, "settled", actor)
    cp = db.get(Counterparty, p.counterparty_id)
    if cp.first_paid_at is None and p.amount_cents:
        cp.first_paid_at = now
    if p.transaction_code in ("23", "28", "33", "38") and cp.validation_status == "prenote_sent":
        cp.validation_status = "verified"


def reconcile(db: Session, f: AchFile) -> dict:
    """Three-way match: FedACH settlement advice vs internal ledger vs core postings."""
    payments = db.scalars(select(Payment).where(Payment.file_id == f.id)).all()
    settled = [p for p in payments if p.status in ("settled", "returned", "reversed")]
    advice_net = sum(p.amount_cents if p.direction == "debit" else -p.amount_cents for p in settled)
    ledger_net = ledger.file_frb_net(db, f.id)
    funded = {p.originator_id for p in settled if p.originator.settlement_account_number}
    core_net = sum(core_banking.net_for_refs(db, f"pay:{p.id}:") for p in settled if p.originator_id in funded)
    expected_core = sum(p.amount_cents if p.direction == "debit" else -p.amount_cents
                        for p in settled if p.originator_id in funded)
    status = "matched" if advice_net == ledger_net and core_net == expected_core else "break"
    run = ReconciliationRun(file_id=f.id, settlement_date=f.settlement_date, window_code=f.window_code,
                            advice_net_cents=advice_net, ledger_net_cents=ledger_net, core_net_cents=core_net, status=status)
    db.add(run)
    if status == "break":
        open_incident(db, f"recon:{f.id}", "P1", "reconciliation_break",
                      f"Reconciliation break on file {f.id} ({f.window_code})",
                      {"advice_net": advice_net, "ledger_net": ledger_net, "core_net": core_net, "expected_core": expected_core})
    return {"file_id": f.id, "window": f.window_code, "status": status, "advice_net_cents": advice_net,
            "ledger_net_cents": ledger_net, "core_net_cents": core_net}
