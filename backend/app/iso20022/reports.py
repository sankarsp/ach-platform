"""Outbound ISO 20022: pain.002 status reports, camt.054 notifications, camt.053 statements.

pain.002 is generated live from the current payment states, so asking again
later reports settlement (ACSC) or a return (RJCT with the mapped reason).
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..domain.windows import ET, UTC
from ..models import CoreAccount, CorePosting, IsoMessage, NocRecord, Originator, Payment, ReturnRecord
from ..domain import rules as RL
from ..services.common import DomainError
from . import codes
from .intake import group_status
from .xmlutil import E, amt, document, max_text, serialize

PAIN002 = "pain.002.001.10"
CAMT054 = "camt.054.001.08"
CAMT053 = "camt.053.001.08"


def _msg_id(prefix: str) -> str:
    return f"{prefix}{utcnow().astimezone(ET).strftime('%Y%m%d%H%M%S')}{secrets.token_hex(3).upper()}"[:35]


def _iso_dt(dt: datetime) -> str:
    return dt.astimezone(ET).isoformat(timespec="seconds")


def _agent(parent, tag: str):
    s = get_settings()
    a = E(parent, tag)
    fi = E(a, "FinInstnId")
    E(fi, "BICFI", s.bank_bic)
    cs = E(fi, "ClrSysMmbId")
    E(E(cs, "ClrSysId"), "Cd", "USABA")
    E(cs, "MmbId", s.bank_routing)
    return a


def _rsn(parent, code: str | None, info: str):
    if not code and not info:
        return
    sri = E(parent, "StsRsnInf")
    if code:
        E(E(sri, "Rsn"), "Cd", code)
    if info:
        E(sri, "AddtlInf", max_text(info, 105))


# ------------------------------------------------------------------ pain.002
def tx_status(db: Session, r: dict) -> tuple[str, str | None, str, Payment | None]:
    """(TxSts, reason code, additional info, payment) for one transaction result, using live payment state."""
    if not r.get("payment_id"):
        return r["status"], r.get("reason"), r.get("info", ""), None
    p = db.get(Payment, r["payment_id"])
    st = codes.TX_STATUS.get(p.status, "ACTC")
    if p.status == "returned":
        rc = RL.RETURN_CODES.get(p.return_code)
        return st, codes.NACHA_TO_ISO.get(p.return_code, "NARR"), f"Nacha {p.return_code} {rc.description if rc else ''}".strip(), p
    if p.status == "held":
        return st, "NARR", p.status_reason or "Held for review", p
    if p.status in ("rejected", "cancelled"):
        return st, "NARR", p.status_reason or p.status, p
    if p.status == "reversed":
        return st, None, "Settled; later reversed", p
    if p.status == "settled":
        return st, None, f"Settled {p.settlement_date} ({p.window_code})", p
    return st, None, f"{p.sec_code} {p.window_code}, settles {p.settlement_date}", p


def pain002(db: Session, m: IsoMessage, store: bool = True) -> IsoMessage:
    if m.direction != "inbound":
        raise DomainError("not_inbound", "Status reports are produced for received pain.001/pain.008 messages")
    org = db.get(Originator, m.originator_id)
    now = utcnow()
    rows = [(r, *tx_status(db, r)) for r in m.results]
    statuses = [s for _, s, _, _, _ in rows]
    grp = m.status if m.status == "RJCT" and m.status_reason else group_status(statuses)

    doc = document(PAIN002)
    rpt = E(doc, "CstmrPmtStsRpt")
    gh = E(rpt, "GrpHdr")
    msg_id = _msg_id("STS")
    E(gh, "MsgId", msg_id)
    E(gh, "CreDtTm", _iso_dt(now))
    ip = E(gh, "InitgPty")
    E(ip, "Nm", get_settings().bank_name)
    _agent(gh, "DbtrAgt" if m.msg_type.startswith("pain.001") else "CdtrAgt")

    og = E(rpt, "OrgnlGrpInfAndSts")
    E(og, "OrgnlMsgId", m.msg_id)
    E(og, "OrgnlMsgNmId", m.msg_type)
    E(og, "OrgnlCreDtTm", _iso_dt(m.created_at))
    E(og, "OrgnlNbOfTxs", m.nb_of_txs)
    E(og, "OrgnlCtrlSum", amt(m.ctrl_sum_cents))
    E(og, "GrpSts", grp)
    if m.status_reason:
        code, _, info = m.status_reason.partition(": ")
        _rsn(og, code, info)
    counts: dict[str, list] = {}
    for _, s, _, _, _ in rows:
        counts.setdefault(s, []).append(1)
    for s, lst in sorted(counts.items()):
        nb = E(og, "NbOfTxsPerSts")
        E(nb, "DtldNbOfTxs", len(lst))
        E(nb, "DtldSts", s)

    by_pi: dict[str, list] = {}
    for row in rows:
        by_pi.setdefault(row[0]["pmt_inf_id"], []).append(row)
    for pi_id, items in by_pi.items():
        pi = E(rpt, "OrgnlPmtInfAndSts")
        E(pi, "OrgnlPmtInfId", pi_id)
        E(pi, "PmtInfSts", group_status([s for _, s, _, _, _ in items]))
        for r, s, code, info, p in items:
            tx = E(pi, "TxInfAndSts")
            if r.get("instr_id"):
                E(tx, "OrgnlInstrId", r["instr_id"])
            E(tx, "OrgnlEndToEndId", r["end_to_end_id"] or "NOTPROVIDED")
            if r.get("uetr"):
                E(tx, "OrgnlUETR", r["uetr"])
            E(tx, "TxSts", s)
            _rsn(tx, code, info)
            if p is not None:
                E(tx, "AcctSvcrRef", p.id)
                if p.trace_number:
                    E(tx, "ClrSysRef", p.trace_number)
            ref = E(tx, "OrgnlTxRef")
            amt_el = E(ref, "Amt")
            E(amt_el, "InstdAmt", amt(r["amount_cents"]), Ccy="USD")
            if p is not None:
                d = E(ref, "ReqdExctnDt" if p.direction == "credit" else "ReqdColltnDt")
                if p.direction == "credit":
                    E(d, "Dt", p.requested_effective_date.isoformat())
                else:
                    d.text = p.requested_effective_date.isoformat()
    xml = serialize(doc)
    out = IsoMessage(direction="outbound", msg_type=PAIN002, msg_id=msg_id, originator_id=org.id if org else None,
                     related_id=m.id, status=grp, nb_of_txs=m.nb_of_txs, ctrl_sum_cents=m.ctrl_sum_cents,
                     content=xml, results=[])
    if store:
        db.add(out)
        db.flush()
    return out


# ------------------------------------------------------------------ helpers for cash reports
def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=ET).astimezone(UTC)
    return start, start + timedelta(days=1)


def _acct(parent, org: Originator):
    a = E(parent, "Acct")
    E(E(E(a, "Id"), "Othr"), "Id", org.settlement_account_number or org.company_id)
    E(a, "Ccy", "USD")
    E(E(a, "Ownr"), "Nm", org.name)
    _agent(a, "Svcr")


def _btc(parent, family: str, sub: str, prtry: str):
    b = E(parent, "BkTxCd")
    d = E(b, "Domn")
    E(d, "Cd", "PMNT")
    f = E(d, "Fmly")
    E(f, "Cd", family)
    E(f, "SubFmlyCd", sub)
    pr = E(b, "Prtry")
    E(pr, "Cd", prtry)
    E(pr, "Issr", "NACHA")


def _entry(parent, *, ref: str, cents: int, cdt: bool, book: date, value: date, svcr_ref: str,
           family: str, sub: str, prtry: str, p: Payment | None, reversal: bool = False, rtr: tuple | None = None):
    n = E(parent, "Ntry")
    E(n, "NtryRef", ref[:35])
    E(n, "Amt", amt(cents), Ccy="USD")
    E(n, "CdtDbtInd", "CRDT" if cdt else "DBIT")
    if reversal:
        E(n, "RvslInd", "true")
    E(E(n, "Sts"), "Cd", "BOOK")
    E(E(n, "BookgDt"), "Dt", book.isoformat())
    E(E(n, "ValDt"), "Dt", value.isoformat())
    E(n, "AcctSvcrRef", svcr_ref[:35])
    _btc(n, family, sub, prtry)
    if p is None:
        return n
    td = E(E(n, "NtryDtls"), "TxDtls")
    refs = E(td, "Refs")
    iso = (p.metadata_ or {}).get("iso", {})
    E(refs, "AcctSvcrRef", p.id)
    if iso.get("instr_id"):
        E(refs, "InstrId", iso["instr_id"])
    E(refs, "EndToEndId", iso.get("end_to_end_id") or "NOTPROVIDED")
    if iso.get("uetr"):
        E(refs, "UETR", iso["uetr"])
    if p.trace_number:
        E(refs, "ClrSysRef", p.trace_number)
    E(td, "Amt", amt(p.amount_cents), Ccy="USD")
    E(td, "CdtDbtInd", "CRDT" if cdt else "DBIT")
    rp = E(td, "RltdPties")
    role = "Cdtr" if p.direction == "credit" else "Dbtr"
    E(E(E(rp, role), "Pty"), "Nm", p.counterparty.name)
    if p.addenda:
        ri = E(td, "RmtInf")
        for a in p.addenda[:5]:
            E(ri, "Ustrd", max_text(a, 140))
    if rtr:
        ri = E(td, "RtrInf")
        E(E(ri, "Rsn"), "Cd", rtr[0])
        E(ri, "AddtlInf", max_text(rtr[1], 105))
    return n


# ------------------------------------------------------------------ camt.054
def camt054(db: Session, org: Originator, day: date) -> IsoMessage:
    """Debit/credit notification for one originator and booking day: settlements and returns."""
    start, end = _day_bounds(day)
    now = utcnow()
    settled = db.scalars(select(Payment).where(Payment.originator_id == org.id, Payment.settled_at >= start,
                                               Payment.settled_at < end, Payment.amount_cents > 0)
                         .order_by(Payment.settled_at)).all()
    returns = db.scalars(select(ReturnRecord).where(ReturnRecord.originator_id == org.id, ReturnRecord.direction == "received",
                                                    ReturnRecord.at >= start, ReturnRecord.at < end)).all()
    nocs = db.scalars(select(NocRecord).where(NocRecord.originator_id == org.id, NocRecord.direction == "received",
                                              NocRecord.at >= start, NocRecord.at < end)).all()
    doc = document(CAMT054)
    root = E(doc, "BkToCstmrDbtCdtNtfctn")
    gh = E(root, "GrpHdr")
    msg_id = _msg_id("NTF")
    E(gh, "MsgId", msg_id)
    E(gh, "CreDtTm", _iso_dt(now))
    nt = E(root, "Ntfctn")
    E(nt, "Id", f"{msg_id}-1"[:35])
    E(nt, "CreDtTm", _iso_dt(now))
    _acct(nt, org)
    for p in settled:
        cdt = p.direction == "debit"     # collected debits credit the originator; credits debit it
        _entry(nt, ref=p.trace_number or p.id, cents=p.amount_cents, cdt=cdt, book=day, value=p.settlement_date or day,
               svcr_ref=p.id, family="IDDT" if cdt else "ICDT", sub="PMDD" if cdt else "ACDT",
               prtry=f"ACH-{p.sec_code}", p=p)
    for r in returns:
        p = db.get(Payment, r.payment_id)
        cdt = p.direction == "credit"    # a returned credit gives money back; a returned debit takes it back
        rc = RL.RETURN_CODES.get(r.reason_code)
        _entry(nt, ref=f"RTN{p.trace_number or p.id}", cents=r.amount_cents, cdt=cdt, book=day, value=day,
               svcr_ref=r.id, family="ICDT" if p.direction == "credit" else "IDDT", sub="ARET",
               prtry=f"ACH-RETURN-{r.reason_code}", p=p, reversal=True,
               rtr=(codes.NACHA_TO_ISO.get(r.reason_code, "NARR"), f"Nacha {r.reason_code} {rc.description if rc else ''}"))
    if nocs:
        texts = []
        for n in nocs:
            p = db.get(Payment, n.payment_id) if n.payment_id else None
            e2e = ((p.metadata_ or {}).get("iso", {}).get("end_to_end_id") if p else None) or n.original_trace
            texts.append(f"NOC {n.change_code} on {e2e}: {codes.NOC_TEXT.get(n.change_code, '')}; corrected data {n.corrected_data.strip()}")
        E(nt, "AddtlNtfctnInf", max_text(" | ".join(texts), 500))
    xml = serialize(doc)
    out = IsoMessage(direction="outbound", msg_type=CAMT054, msg_id=msg_id, originator_id=org.id, status="BOOK",
                     nb_of_txs=len(settled) + len(returns),
                     ctrl_sum_cents=sum(p.amount_cents for p in settled) + sum(r.amount_cents for r in returns),
                     content=xml, results=[{"day": day.isoformat(), "settled": len(settled), "returns": len(returns), "nocs": len(nocs)}])
    db.add(out)
    db.flush()
    return out


# ------------------------------------------------------------------ camt.053
def camt053(db: Session, org: Originator, day: date) -> IsoMessage:
    """End-of-day statement of the originator's settlement account at this bank."""
    if not org.settlement_account_number:
        raise DomainError("no_settlement_account", "Originator has no settlement account at this bank")
    acct = db.scalar(select(CoreAccount).where(CoreAccount.account_number == org.settlement_account_number))
    if acct is None:
        raise DomainError("no_settlement_account", "Settlement account not found in core banking")
    start, end = _day_bounds(day)
    now = utcnow()
    after_start = db.scalars(select(CorePosting).where(CorePosting.core_account_id == acct.id, CorePosting.at >= start)).all()
    opening = acct.ledger_balance_cents - sum(x.amount_cents for x in after_start)
    day_rows = sorted([x for x in after_start if x.at < end], key=lambda x: x.id)
    closing = opening + sum(x.amount_cents for x in day_rows)
    cr = [x for x in day_rows if x.amount_cents > 0]
    dr = [x for x in day_rows if x.amount_cents < 0]

    doc = document(CAMT053)
    root = E(doc, "BkToCstmrStmt")
    gh = E(root, "GrpHdr")
    msg_id = _msg_id("STM")
    E(gh, "MsgId", msg_id)
    E(gh, "CreDtTm", _iso_dt(now))
    st = E(root, "Stmt")
    E(st, "Id", f"{org.company_id}-{day.isoformat()}")
    E(st, "CreDtTm", _iso_dt(now))
    fr = E(st, "FrToDt")
    E(fr, "FrDtTm", _iso_dt(start))
    E(fr, "ToDtTm", _iso_dt(end - timedelta(seconds=1)))
    _acct(st, org)
    for code, val in (("OPBD", opening), ("CLBD", closing)):
        b = E(st, "Bal")
        E(E(E(b, "Tp"), "CdOrPrtry"), "Cd", code)
        E(b, "Amt", amt(abs(val)), Ccy="USD")
        E(b, "CdtDbtInd", "CRDT" if val >= 0 else "DBIT")
        E(E(b, "Dt"), "Dt", day.isoformat())
    ts = E(st, "TxsSummry")
    tot = E(ts, "TtlNtries")
    E(tot, "NbOfNtries", len(day_rows))
    E(tot, "Sum", amt(sum(abs(x.amount_cents) for x in day_rows)))
    net = sum(x.amount_cents for x in day_rows)
    tn = E(tot, "TtlNetNtry")
    E(tn, "Amt", amt(abs(net)))
    E(tn, "CdtDbtInd", "CRDT" if net >= 0 else "DBIT")
    for tag, rows in (("TtlCdtNtries", cr), ("TtlDbtNtries", dr)):
        t = E(ts, tag)
        E(t, "NbOfNtries", len(rows))
        E(t, "Sum", amt(sum(abs(x.amount_cents) for x in rows)))
    for x in day_rows:
        parts = x.ref.split(":")
        p = db.get(Payment, parts[1]) if len(parts) >= 3 and parts[0] == "pay" else None
        stage = parts[2] if p else "other"
        cdt = x.amount_cents > 0
        if stage == "return" and p:
            rc = RL.RETURN_CODES.get(p.return_code)
            _entry(st, ref=x.ref, cents=abs(x.amount_cents), cdt=cdt, book=day, value=day, svcr_ref=str(x.id),
                   family="ICDT" if p.direction == "credit" else "IDDT", sub="ARET", prtry=f"ACH-RETURN-{p.return_code}",
                   p=p, reversal=True, rtr=(codes.NACHA_TO_ISO.get(p.return_code, "NARR"),
                                            f"Nacha {p.return_code} {rc.description if rc else ''}"))
        elif p:
            _entry(st, ref=x.ref, cents=abs(x.amount_cents), cdt=cdt, book=day, value=p.settlement_date or day,
                   svcr_ref=str(x.id), family="IDDT" if p.direction == "debit" else "ICDT",
                   sub="PMDD" if p.direction == "debit" else "ACDT", prtry=f"ACH-{p.sec_code}", p=p)
        else:
            n = _entry(st, ref=x.ref, cents=abs(x.amount_cents), cdt=cdt, book=day, value=day, svcr_ref=str(x.id),
                       family="MCOP" if cdt else "MDOP", sub="OTHR", prtry="OTHER", p=None)
            E(n, "AddtlNtryInf", max_text(x.description, 500))
    xml = serialize(doc)
    out = IsoMessage(direction="outbound", msg_type=CAMT053, msg_id=msg_id, originator_id=org.id, status="BOOK",
                     nb_of_txs=len(day_rows), ctrl_sum_cents=sum(abs(x.amount_cents) for x in day_rows), content=xml,
                     results=[{"day": day.isoformat(), "opening_cents": opening, "closing_cents": closing}])
    db.add(out)
    db.flush()
    return out
