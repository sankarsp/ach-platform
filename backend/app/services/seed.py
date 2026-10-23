"""Sandbox seed data: institution accounts, originators, receivers, API clients and a demo day."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..domain.windows import ET
from ..models import ApiClient, CoreAccount, Originator, SanctionsEntry
from . import ledger
from .common import hash_key
from .originations import (PaymentRequest, activate_originator, create_counterparty, create_originator,
                           create_payment, validate_account)

DEMO_KEYS = {
    "sk_ops_demo": ("Operations console", "operator", None),
    "sk_admin_demo": ("Platform admin", "admin", None),
    "sk_acme_demo": ("Acme Payroll API", "originator", "1111111111"),
    "sk_bright_demo": ("Brightway Utilities API", "originator", "2222222222"),
    "sk_north_demo": ("Northwind Supply API", "originator", "3333333333"),
}

# Fictional placeholder entries. Production loads the OFAC SDN and consolidated lists daily.
SANCTIONS = [
    ("BLACKSTAR TRADING COMPANY", "SDN", "SAMPLE", "IR"),
    ("VOLKOV IMPORT EXPORT", "SDN", "SAMPLE", "RU"),
    ("IVAN PETROVICH SAMPLENKO", "SDN", "SAMPLE", "RU"),
    ("GOLDEN CRESCENT EXCHANGE", "SDN", "SAMPLE", "SY"),
    ("NORTH STAR SHIPPING LLC", "SDN", "SAMPLE", "KP"),
]


def seed(db: Session, demo_activity: bool = True) -> dict:
    if db.scalar(select(Originator.id).limit(1)):
        return {"seeded": False}
    for code in ("FRB_SETTLEMENT", "ACH_CLEARING_OUT", "ACH_RECEIVED_SUSPENSE", "FEE_INCOME"):
        ledger.account(db, code)
    for n, l, p, c in SANCTIONS:
        db.add(SanctionsEntry(name=n, list_name=l, program=p, country=c))

    today = utcnow().astimezone(ET).date()
    core = [
        ("9000000001", "Acme Payroll Services Inc", "business", "checking", "open", 5_000_000_00, date(2018, 1, 5)),
        ("9000000002", "Brightway Utilities Co", "business", "checking", "open", 250_000_00, date(2016, 3, 1)),
        ("9000000003", "Northwind Supply Corp", "business", "checking", "open", 2_000_000_00, date(2019, 7, 9)),
        ("5550001111", "Maria Lopez", "consumer", "checking", "open", 4_200_00, date(2015, 6, 2)),
        ("5550002222", "James Chen", "consumer", "savings", "open", 18_000_00, date(2020, 2, 14)),
        ("5550003333", "Priya Raman", "consumer", "checking", "open", 300_00, today - timedelta(days=9)),
        ("5550004444", "Closed Account Holder", "consumer", "checking", "closed", 0, date(2012, 1, 1)),
        ("7770001111", "Harbor Logistics LLC", "business", "checking", "open", 90_000_00, date(2017, 9, 1)),
    ]
    for num, name, otype, atype, status, bal, opened in core:
        acct = CoreAccount(account_number=num, owner_name=name, owner_type=otype, account_type=atype, status=status,
                           ledger_balance_cents=bal, available_balance_cents=bal, opened_on=opened)
        if num == "7770001111":
            acct.allowed_debit_company_ids = ["2222222222"]   # debit filter: only Brightway may debit
        db.add(acct)
    db.flush()

    now = utcnow()
    orgs = {}
    for name, cid, secs, debits, funding, rating, tps in [
        ("Acme Payroll Services", "1111111111", ["PPD", "CCD"], False, "9000000001", "low", True),
        ("Brightway Utilities", "2222222222", ["PPD", "WEB", "TEL", "CCD"], True, "9000000002", "medium", False),
        ("Northwind Supply", "3333333333", ["CCD", "CTX", "IAT"], True, "9000000003", "medium", False),
    ]:
        o = create_originator(db, {"name": name, "company_id": cid, "allowed_sec_codes": secs, "allows_debits": debits,
                                   "settlement_account_number": funding, "risk_rating": rating,
                                   "is_third_party_sender": tps, "kyb_status": "approved", "agreement_signed_at": now,
                                   "daily_limit_cents": 2_500_000_00, "per_entry_limit_cents": 1_500_000_00,
                                   "address": {"street": "100 Main St", "city": "Newark", "state": "NJ",
                                               "country": "US", "postal": "07102"}}, "seed")
        activate_originator(db, o, "seed")
        orgs[cid] = o

    for raw, (name, role, cid) in DEMO_KEYS.items():
        db.add(ApiClient(name=name, key_hash=hash_key(raw), key_prefix=raw[:8], role=role,
                         originator_id=orgs[cid].id if cid else None))

    cps = {}
    for cid, name, ptype, rtn, acct, atype in [
        ("1111111111", "Maria Lopez", "consumer", "021000021", "5550001111", "checking"),
        ("1111111111", "Daniel Brooks", "consumer", "011000015", "4412009876", "checking"),
        ("1111111111", "Aisha Khan", "consumer", "026009593", "7788990011", "savings"),
        ("2222222222", "James Chen", "consumer", "021000021", "5550002222", "savings"),
        ("2222222222", "Olivia Martin", "consumer", "011000015", "3300112233", "checking"),
        ("2222222222", "Harbor Logistics LLC", "business", "021000021", "7770001111", "checking"),
        ("3333333333", "Pacific Components Inc", "business", "026009593", "6600778899", "checking"),
        ("3333333333", "Blackstar Trading Co", "business", "011000015", "1212121212", "checking"),
        ("3333333333", "Midlands Precision Ltd", "business", "011000015", "GB29NWBK60161331926819", "checking"),
    ]:
        data = {"name": name, "party_type": ptype, "routing_number": rtn, "account_number": acct, "account_type": atype}
        if acct.startswith("GB"):
            data.update(country="GB", address={"street": "10 High St", "city": "Birmingham", "country": "GB", "postal": "B11AA"})
        cp = create_counterparty(db, orgs[cid], data, "seed")
        if name not in ("Olivia Martin",):
            validate_account(db, cp, "instant", "seed")
        cps[name] = cp
    db.flush()

    summary = {"seeded": True, "api_keys": {k: v[0] for k, v in DEMO_KEYS.items()}}
    if not demo_activity:
        return summary

    acme, bright, north = orgs["1111111111"], orgs["2222222222"], orgs["3333333333"]
    nxt = today + timedelta(days=1)
    reqs = [
        (acme, PaymentRequest(cps["Maria Lopez"].id, 2_150_00, "credit", "PPD", today, "payroll", same_day=True, idempotency_key="seed-1")),
        (acme, PaymentRequest(cps["Daniel Brooks"].id, 1_980_00, "credit", "PPD", nxt, "payroll", idempotency_key="seed-2")),
        (acme, PaymentRequest(cps["Aisha Khan"].id, 2_430_00, "credit", "PPD", nxt, "payroll", idempotency_key="seed-3")),
        (bright, PaymentRequest(cps["James Chen"].id, 142_37, "debit", "PPD", nxt, "bill_payment", "UTILITY", idempotency_key="seed-4")),
        (bright, PaymentRequest(cps["Olivia Martin"].id, 89_10, "debit", "WEB", nxt, "purchase", idempotency_key="seed-5")),
        (bright, PaymentRequest(cps["Harbor Logistics LLC"].id, 3_400_00, "debit", "CCD", nxt, "bill_payment", "SERVICES", idempotency_key="seed-6")),
        (north, PaymentRequest(cps["Pacific Components Inc"].id, 48_250_00, "credit", "CTX", today, "vendor", "VENDOR PAY",
                               same_day=True, addenda=["RMR*IV*INV-10023*48250\\", "REF*PO*77821\\"], idempotency_key="seed-7")),
        (north, PaymentRequest(cps["Blackstar Trading Co"].id, 12_000_00, "credit", "CCD", nxt, "vendor", "VENDOR PAY", idempotency_key="seed-8")),
        (north, PaymentRequest(cps["Midlands Precision Ltd"].id, 9_800_00, "credit", "IAT", nxt, "cross_border", "TRADE",
                               iat={"transaction_type": "BUS", "destination_country": "GB",
                                    "originator": {"city": "Newark", "state": "NJ", "country": "US", "postal": "07102"},
                                    "receiver": {"name": "Midlands Precision Ltd", "id": "MPL-01", "street": "10 High St",
                                                 "city": "Birmingham", "state": "", "country": "GB", "postal": "B11AA"},
                                    "rdfi": {"name": "Gateway Bank NA", "id": "011000015", "id_qualifier": "01", "country": "US"}},
                               addenda=["Invoice MPL-5531"], idempotency_key="seed-9")),
    ]
    for org, r in reqs:
        create_payment(db, org, r, "seed", now)
    db.flush()
    summary["payments"] = len(reqs)
    return summary
