"""pain.001 (credit transfer initiation) and pain.008 (direct debit initiation) intake.

Every transaction is mapped to a PaymentRequest and goes through exactly the
same pipeline as the REST API: rules catalog, window planning, sanctions,
fraud, AML, limits. Nothing in ISO 20022 bypasses a control.

Mapping (credit):
  PmtTpInf/LclInstrm (Cd|Prtry) = PPD/CCD/CTX/WEB/IAT  -> SEC code (explicit wins)
  creditor country != US                               -> IAT
  creditor is an organisation or business category     -> CCD, or CTX with structured/multi-line remittance
  otherwise                                            -> PPD
  CtgyPurp/Cd (SALA, PENS, SUPP, TAXS, ...)            -> purpose + company entry description
  SvcLvl/Cd = SDVA                                     -> Same Day ACH
  ReqdExctnDt                                          -> effective entry date
  RmtInf/Ustrd and Strd                                -> addenda (CTX: EDI RMR segments)
Mapping (debit): the same, plus DrctDbtTx/MndtRltdInf (MndtId, DtOfSgntr) is
required as authorization evidence and stored with the payment.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..domain.windows import ET
from ..domain import rules as RL
from ..models import Counterparty, IsoMessage, Originator
from ..services import originations as svc
from ..services.common import DomainError, audit, detokenize
from . import codes
from .xmlutil import Node, parse, to_cents

KINDS = {"CstmrCdtTrfInitn": ("credit", "pain.001", "PmtInf/CdtTrfTxInf", "CdtTrfTxInf"),
         "CstmrDrctDbtInitn": ("debit", "pain.008", "PmtInf/DrctDbtTxInf", "DrctDbtTxInf")}


# ------------------------------------------------------------------ element helpers
def party(n: Node | None) -> dict:
    if n is None:
        return {}
    org_id = n.text("Id/OrgId/Othr/Id") or n.text("Id/OrgId/LEI") or n.text("Id/OrgId/AnyBIC")
    prv_id = n.text("Id/PrvtId/Othr/Id")
    return {"name": n.text("Nm"), "is_org": n.find("Id/OrgId") is not None, "id": org_id or prv_id,
            "country": n.text("PstlAdr/Ctry") or n.text("CtryOfRes"), "street": " ".join(
                x for x in (n.text("PstlAdr/BldgNb"), n.text("PstlAdr/StrtNm")) if x) or n.text("PstlAdr/AdrLine"),
            "city": n.text("PstlAdr/TwnNm"), "state": n.text("PstlAdr/CtrySubDvsn"), "postal": n.text("PstlAdr/PstCd")}


def agent(n: Node | None) -> dict:
    if n is None:
        return {}
    return {"routing": n.text("FinInstnId/ClrSysMmbId/MmbId"), "bic": n.text("FinInstnId/BICFI") or n.text("FinInstnId/BIC"),
            "name": n.text("FinInstnId/Nm"), "country": n.text("FinInstnId/PstlAdr/Ctry")}


def account(n: Node | None) -> dict:
    if n is None:
        return {}
    tp = n.text("Tp/Cd")
    return {"id": n.text("Id/Othr/Id") or n.text("Id/IBAN"), "iban": bool(n.text("Id/IBAN")),
            "type": "savings" if tp == "SVGS" else "checking"}


def remittance(tx: Node) -> tuple[list[str], list[str]]:
    ustrd = [u.text() for u in tx.findall("RmtInf/Ustrd") if u.text()]
    strd = []
    for s in tx.findall("RmtInf/Strd"):
        nb = s.text("RfrdDocInf/Nb") or s.text("CdtrRefInf/Ref")
        amt_ = s.text("RfrdDocAmt/RmtdAmt") or s.text("RfrdDocAmt/DuePyblAmt")
        if nb:
            strd.append(f"RMR*IV*{nb}*PI*{amt_}\\" if amt_ else f"RMR*IV*{nb}\\")
    return ustrd, strd


def _chunks(lines: list[str], n: int = 80) -> list[str]:
    out = []
    for line in lines:
        out.extend(line[i:i + n] for i in range(0, len(line), n))
    return out


def _date(n: Node, path: str) -> date | None:
    v = n.text(path + "/Dt") or n.text(path)
    try:
        return date.fromisoformat(v[:10]) if v else None
    except ValueError:
        raise DomainError("bad_date", f"Invalid date {v!r} in {path}")


# ------------------------------------------------------------------ originator / counterparty
def resolve_originator(db: Session, root: Node, kind: str, scoped: Originator | None) -> Originator:
    if scoped is not None:
        return scoped
    who = "Dbtr" if kind == "credit" else "Cdtr"
    acct_path = "DbtrAcct" if kind == "credit" else "CdtrAcct"
    candidates = [root.text("GrpHdr/InitgPty/Id/OrgId/Othr/Id")]
    for p in root.findall("PmtInf"):
        candidates += [p.text(f"{who}/Id/OrgId/Othr/Id"), p.text("CdtrSchmeId/Id/OrgId/Othr/Id")]
    for c in filter(None, candidates):
        o = db.scalar(select(Originator).where(Originator.company_id == c))
        if o:
            return o
    for p in root.findall("PmtInf"):
        a = account(p.find(acct_path)).get("id")
        if a:
            o = db.scalar(select(Originator).where(Originator.settlement_account_number == a))
            if o:
                return o
    raise DomainError("originator_unresolved",
                      "Cannot identify the originator: set InitgPty or the debtor/creditor Id to the Nacha company ID, "
                      "or use the originator's settlement account", 422)


def find_or_create_counterparty(db: Session, org: Originator, name: str, routing: str, acct: dict,
                                business: bool, country: str, address: dict, actor: str) -> Counterparty:
    for c in db.scalars(select(Counterparty).where(Counterparty.originator_id == org.id,
                                                   Counterparty.routing_number == routing)):
        if detokenize(db, c.account_token) == acct["id"]:
            return c
    return svc.create_counterparty(db, org, {
        "name": name or "Receiver", "party_type": "business" if business else "consumer",
        "routing_number": routing, "account_number": acct["id"], "account_type": acct.get("type", "checking"),
        "country": country or "US", "address": address}, actor)


# ------------------------------------------------------------------ main entry point
def ingest(db: Session, xml: str, scoped: Originator | None, actor: str, now: datetime | None = None,
           expect: str | None = None) -> IsoMessage:
    now = now or utcnow()
    msg_def, root = parse(xml)
    if root.tag not in KINDS:
        raise DomainError("unsupported_message", f"<{root.tag}> is not supported; send pain.001 or pain.008")
    kind, family, tx_path, tx_tag = KINDS[root.tag]
    if expect and family != expect:
        raise DomainError("wrong_endpoint", f"This endpoint accepts {expect}; the message is {family}")
    if msg_def and not msg_def.startswith(family):
        raise DomainError("namespace_mismatch", f"Namespace {msg_def} does not match <{root.tag}>")
    msg_id = root.text("GrpHdr/MsgId")
    if not msg_id or len(msg_id) > 35:
        raise DomainError("bad_msg_id", "GrpHdr/MsgId is required (max 35 characters)")
    org = resolve_originator(db, root, kind, scoped)
    if db.scalar(select(IsoMessage.id).where(IsoMessage.direction == "inbound", IsoMessage.originator_id == org.id,
                                             IsoMessage.msg_id == msg_id)):
        raise DomainError("duplicate_message", f"Message {msg_id} was already received (DU01)", 409)

    txs = root.findall(tx_path)
    total = 0
    for t in txs:
        try:
            total += to_cents(t.text("InstdAmt") or t.text("Amt/InstdAmt"))
        except DomainError:
            pass
    m = IsoMessage(direction="inbound", msg_type=msg_def or family, msg_id=msg_id, originator_id=org.id,
                   nb_of_txs=len(txs), ctrl_sum_cents=total, content=xml if isinstance(xml, str) else xml.decode())
    db.add(m)
    db.flush()

    results: list[dict] = []
    declared_n = root.text("GrpHdr/NbOfTxs")
    declared_sum = root.text("GrpHdr/CtrlSum")
    group_reason = None
    if not declared_n.isdigit() or int(declared_n) != len(txs):
        group_reason = ("AM18", f"GrpHdr/NbOfTxs {declared_n or 'missing'} but message holds {len(txs)} transactions")
    elif declared_sum:
        try:
            if to_cents(declared_sum) != total:
                group_reason = ("AM16", f"GrpHdr/CtrlSum {declared_sum} does not equal the sum of transactions")
        except DomainError:
            group_reason = ("AM16", "GrpHdr/CtrlSum is not a valid amount")
    if group_reason:
        for p in root.findall("PmtInf"):
            for t in p.findall(tx_tag):
                results.append(_result(p, t, "RJCT", *group_reason))
        m.status, m.status_reason, m.results = "RJCT", f"{group_reason[0]}: {group_reason[1]}", results
        audit(db, actor, "iso.rejected", "iso_message", m.id, msg_id=msg_id, reason=group_reason[0])
        return m

    seen_e2e: set[str] = set()
    for p in root.findall("PmtInf"):
        results.extend(_payment_info(db, m, org, p, kind, tx_tag, seen_e2e, actor, now))
    m.results = results
    m.status = group_status([r["status"] for r in results])
    audit(db, actor, "iso.received", "iso_message", m.id, msg_id=msg_id, type=m.msg_type, txs=len(results), status=m.status)
    return m


def group_status(statuses: list[str]) -> str:
    if not statuses:
        return "RJCT"
    if all(s == "RJCT" for s in statuses):
        return "RJCT"
    if any(s == "RJCT" for s in statuses):
        return "PART"
    if all(s == "ACSC" for s in statuses):
        return "ACSC"
    return "ACCP" if all(s in ("ACCP", "ACSP", "ACSC", "ACTC") for s in statuses) else "ACTC"


def _result(p: Node, t: Node, status: str, reason: str | None = None, info: str = "", payment_id: str | None = None,
            amount_cents: int | None = None) -> dict:
    if amount_cents is None:
        try:
            amount_cents = to_cents(t.text("InstdAmt") or t.text("Amt/InstdAmt"))
        except DomainError:
            amount_cents = 0
    return {"pmt_inf_id": p.text("PmtInfId"), "instr_id": t.text("PmtId/InstrId"),
            "end_to_end_id": t.text("PmtId/EndToEndId"), "uetr": t.text("PmtId/UETR"),
            "amount_cents": amount_cents, "status": status, "reason": reason, "info": info[:105],
            "payment_id": payment_id}


def _payment_info(db: Session, m: IsoMessage, org: Originator, p: Node, kind: str, tx_tag: str,
                  seen: set[str], actor: str, now: datetime) -> list[dict]:
    txs = p.findall(tx_tag)
    method = p.text("PmtMtd")
    want = "TRF" if kind == "credit" else "DD"
    reject_all = None
    if method != want:
        reject_all = ("AG03", f"PmtMtd {method or 'missing'} is not {want}")
    acct_path = "DbtrAcct" if kind == "credit" else "CdtrAcct"
    own_acct = account(p.find(acct_path)).get("id")
    if not reject_all and own_acct and org.settlement_account_number and own_acct != org.settlement_account_number:
        reject_all = ("AC02" if kind == "credit" else "AC03",
                      f"{acct_path} {own_acct} is not the originator's settlement account")
    if not reject_all and p.text("NbOfTxs") and p.text("NbOfTxs") != str(len(txs)):
        reject_all = ("AM18", "PmtInf/NbOfTxs does not match the transactions in this payment information block")
    if reject_all:
        return [_result(p, t, "RJCT", *reject_all) for t in txs]

    eff = _date(p, "ReqdExctnDt" if kind == "credit" else "ReqdColltnDt") or now.astimezone(ET).date()
    same_day = (p.text("PmtTpInf/SvcLvl/Cd") or "").upper() == "SDVA"
    lcl = (p.text("PmtTpInf/LclInstrm/Prtry") or p.text("PmtTpInf/LclInstrm/Cd")).upper()
    pmt_ctgy = p.text("PmtTpInf/CtgyPurp/Cd").upper()

    out = []
    for t in txs:
        sp = db.begin_nested()
        try:
            res = _transaction(db, m, org, p, t, kind, eff, same_day, lcl, pmt_ctgy, seen, actor, now)
            sp.commit()
        except DomainError as exc:
            sp.rollback()
            res = _result(p, t, "RJCT", codes.DOMAIN_TO_ISO.get(exc.code, "NARR"), exc.message)
        out.append(res)
    return out


def _transaction(db, m, org, p, t, kind, eff, same_day, lcl, pmt_ctgy, seen, actor, now) -> dict:
    e2e = t.text("PmtId/EndToEndId")
    if not e2e:
        raise DomainError("bad_e2e", "PmtId/EndToEndId is required")
    if e2e in seen and e2e != "NOTPROVIDED":
        return _result(p, t, "RJCT", "AM05", f"Duplicate EndToEndId {e2e} in this message")
    seen.add(e2e)

    amount_node = "InstdAmt" if kind == "debit" else "Amt/InstdAmt"
    ccy = t.attr(amount_node, "Ccy")
    if ccy and ccy != "USD":
        return _result(p, t, "RJCT", "AM03", f"Currency {ccy} not supported; ACH settles in USD")
    cents = to_cents(t.text(amount_node))

    if kind == "credit":
        cp_party, cp_agent, cp_acct = party(t.find("Cdtr")), agent(t.find("CdtrAgt")), account(t.find("CdtrAcct"))
    else:
        cp_party, cp_agent, cp_acct = party(t.find("Dbtr")), agent(t.find("DbtrAgt")), account(t.find("DbtrAcct"))
    if not cp_acct.get("id"):
        return _result(p, t, "RJCT", "AC01", "Counterparty account is missing")
    ctgy = (t.text("PmtTpInf/CtgyPurp/Cd") or pmt_ctgy).upper()
    purp = t.text("Purp/Cd").upper()
    country = (cp_party.get("country") or "US").upper()
    ustrd, strd = remittance(t)

    # SEC code
    tx_lcl = (t.text("PmtTpInf/LclInstrm/Prtry") or t.text("PmtTpInf/LclInstrm/Cd")).upper() or lcl
    business = cp_party.get("is_org") or ctgy in codes.BUSINESS_CATEGORIES
    if tx_lcl in RL.SEC_CODES:
        sec = tx_lcl
    elif country != "US":
        sec = "IAT"
    elif business:
        sec = "CTX" if (strd or len(ustrd) > 1) and kind == "credit" else "CCD"
    else:
        sec = "PPD"
    if sec in ("CCD", "CTX"):
        business = True
    elif sec in ("PPD", "WEB", "TEL"):
        business = False

    table = codes.CREDIT_PURPOSE if kind == "credit" else codes.DEBIT_PURPOSE
    purpose, desc = table.get(ctgy) or table.get(purp) or (("other", "PAYMENT") if kind == "credit" else ("bill_payment", "BILL PAY"))
    if purpose == "payroll" and sec != "PPD":
        desc = "PAYROLL"
    if sec == "IAT":
        purpose = "cross_border"

    # Addenda
    if sec == "CTX":
        addenda = strd + _chunks(ustrd)
    elif sec == "IAT":
        addenda = _chunks(ustrd + strd)[:2]
    else:
        lines = _chunks(ustrd + strd)
        addenda = lines[:1]

    # Mandate (authorization evidence) for debits
    metadata = {"iso": {"message_id": m.id, "msg_id": m.msg_id, "pmt_inf_id": p.text("PmtInfId"),
                        "instr_id": t.text("PmtId/InstrId"), "end_to_end_id": e2e, "uetr": t.text("PmtId/UETR"),
                        "category_purpose": ctgy, "purpose": purp}}
    if kind == "debit":
        mid, signed = t.text("DrctDbtTx/MndtRltdInf/MndtId"), t.text("DrctDbtTx/MndtRltdInf/DtOfSgntr")
        if not mid or not signed:
            return _result(p, t, "RJCT", "MD01", "Mandate (MndtId and DtOfSgntr) is required for a direct debit")
        metadata["mandate"] = {"id": mid, "signed_on": signed, "amended": t.text("DrctDbtTx/MndtRltdInf/AmdmntInd")}

    # Counterparty
    s = get_settings()
    routing = cp_agent.get("routing")
    iat = None
    if sec == "IAT":
        routing = routing or cp_agent.get("bic") or "FOREIGN"
        iat = {"transaction_type": codes.IAT_TYPE.get(ctgy, "BUS"), "destination_country": country,
               "destination_currency": "USD",
               "originator": {k: v for k, v in party(p.find("Dbtr" if kind == "credit" else "Cdtr")).items() if k in ("city", "state", "country", "postal", "street") and v},
               "receiver": {"name": cp_party.get("name", ""), "id": cp_party.get("id", "")[:15], "street": cp_party.get("street", ""),
                            "city": cp_party.get("city", ""), "state": cp_party.get("state", ""), "country": country,
                            "postal": cp_party.get("postal", "")},
               "rdfi": {"name": cp_agent.get("name") or cp_agent.get("bic") or "Foreign bank",
                        "id": cp_agent.get("bic") or routing, "id_qualifier": "02" if cp_agent.get("bic") else "01",
                        "country": (cp_agent.get("country") or country)[:3], "gateway_routing": s.iat_gateway_routing}}
    elif not routing:
        return _result(p, t, "RJCT", "RC01", "Counterparty agent ClrSysMmbId/MmbId (ABA routing number) is required")
    cp = find_or_create_counterparty(db, org, cp_party.get("name", ""), routing, cp_acct, business, country,
                                     {k: v for k, v in cp_party.items() if k in ("street", "city", "state", "postal") and v}, actor)

    req = svc.PaymentRequest(counterparty_id=cp.id, amount_cents=cents, direction=kind, sec_code=sec,
                             effective_date=eff, purpose=purpose, company_entry_description=desc, same_day=same_day,
                             addenda=addenda, web_payment_type="S" if sec == "WEB" else None, iat=iat,
                             idempotency_key=f"iso:{m.msg_id}:{e2e}"[:120], metadata=metadata)
    pay = svc.create_payment(db, org, req, actor, now)
    status = codes.TX_STATUS.get(pay.status, "ACTC")
    info = pay.status_reason if pay.status == "held" else f"{sec} {pay.window_code}, settles {pay.settlement_date}"
    return _result(p, t, status, None if status != "PDNG" else "NARR", info or "", pay.id, cents)
