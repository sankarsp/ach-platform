"""End-to-end flows through the REST API (sandbox)."""
from datetime import date, timedelta

from sqlalchemy import select

from app.domain.windows import ET
from app.db import utcnow
from app.models import CoreAccount, Counterparty, Payment
from app.nacha.file import parse_file

from .conftest import ACME, BRIGHT, NORTH, OPS


def _today():
    return utcnow().astimezone(ET).date()


def _cp(client, headers, name):
    return next(c for c in client.get("/v1/counterparties", headers=headers).json() if c["name"] == name)


def _run(client, **kw):
    r = client.post("/v1/ops/run", headers=OPS, json={"force": True, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_auth_required(client):
    assert client.get("/v1/payments").status_code == 401
    assert client.get("/v1/payments", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_seed_states(client):
    pays = client.get("/v1/payments", headers=OPS).json()
    held = [p for p in pays if p["status"] == "held"]
    assert len(pays) == 9
    assert any("sanctions" in (p["status_reason"] or "").lower() for p in held)   # Blackstar Trading Co
    iat = next(p for p in pays if p["sec_code"] == "IAT")
    assert not iat["same_day"]


def test_originator_scoping(client):
    bright_pay = client.get("/v1/payments", headers=BRIGHT).json()[0]
    assert client.get(f"/v1/payments/{bright_pay['id']}", headers=ACME).status_code == 404
    assert all(p["originator_id"] == bright_pay["originator_id"] for p in client.get("/v1/payments", headers=BRIGHT).json())
    assert client.get("/v1/alerts", headers=ACME).status_code == 403


def test_idempotency(client):
    cp = _cp(client, ACME, "Maria Lopez")
    body = {"counterparty_id": cp["id"], "amount_cents": 1000, "direction": "credit", "sec_code": "PPD",
            "effective_date": str(_today() + timedelta(days=3)), "purpose": "payroll"}
    h = {**ACME, "Idempotency-Key": "abc-1"}
    r1 = client.post("/v1/payments", headers=h, json=body)
    r2 = client.post("/v1/payments", headers=h, json=body)
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"] and r2.headers.get("Idempotent-Replayed") == "true"
    r3 = client.post("/v1/payments", headers=h, json={**body, "amount_cents": 2000})
    assert r3.status_code == 409
    assert client.post("/v1/payments", headers=ACME, json=body).status_code == 422   # key required


def test_rules_enforced(client):
    maria = _cp(client, ACME, "Maria Lopez")
    base = {"counterparty_id": maria["id"], "amount_cents": 1000, "direction": "credit",
            "effective_date": str(_today() + timedelta(days=3))}
    r = client.post("/v1/payments", headers={**ACME, "Idempotency-Key": "r1"},
                    json={**base, "sec_code": "PPD", "purpose": "payroll", "company_entry_description": "SALARY"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "entry_description"
    r = client.post("/v1/payments", headers={**ACME, "Idempotency-Key": "r2"},
                    json={**base, "sec_code": "CCD", "purpose": "vendor", "company_entry_description": "VENDOR"})
    assert r.json()["error"]["code"] == "party_mismatch"
    r = client.post("/v1/payments", headers={**ACME, "Idempotency-Key": "r3"},
                    json={**base, "sec_code": "WEB", "purpose": "purchase", "direction": "debit"})
    assert r.json()["error"]["code"] in ("sec_not_allowed", "debits_not_allowed")
    r = client.post("/v1/payments", headers={**ACME, "Idempotency-Key": "r4"},
                    json={**base, "sec_code": "PPD", "purpose": "payroll"})
    assert r.status_code == 201 and r.json()["company_entry_description"] == "PAYROLL"


def test_full_cycle_release_settle_reconcile(client):
    out = _run(client)
    assert out["files_released"] and all(s["status"] == "matched" for s in out["settled"])
    pays = client.get("/v1/payments", headers=OPS).json()
    assert {p["status"] for p in pays} <= {"settled", "held"}
    f = client.get("/v1/files?direction=outbound", headers=OPS).json()[0]
    raw = client.get(f"/v1/files/{f['id']}/raw", headers=OPS).text
    pf = parse_file(raw)
    assert pf.errors == []
    assert client.get("/v1/ledger/trial-balance", headers=OPS).json()["balanced"]
    detail = client.get(f"/v1/payments/{pays[0]['id']}", headers=OPS).json()
    assert [e["to"] for e in detail["events"]][-2:] == ["transmitted", "settled"]


def test_return_blocks_account_and_refunds(client, db):
    _run(client)
    acme_pays = [p for p in client.get("/v1/payments", headers=ACME).json() if p["status"] == "settled"]
    p = acme_pays[0]
    before = db.scalar(select(CoreAccount).where(CoreAccount.account_number == "9000000001")).available_balance_cents
    r = client.post("/v1/sandbox/returns", headers=OPS, json={"payment_id": p["id"], "return_code": "R03"})
    assert r.status_code == 200 and r.json()["returns"] == 1, r.text
    got = client.get(f"/v1/payments/{p['id']}", headers=ACME).json()
    assert got["status"] == "returned" and got["return_code"] == "R03"
    cp = client.get(f"/v1/counterparties/{p['counterparty_id']}", headers=ACME).json()
    assert cp["status"] == "blocked" and cp["validation_status"] == "failed"
    db.expire_all()
    after = db.scalar(select(CoreAccount).where(CoreAccount.account_number == "9000000001")).available_balance_cents
    assert after == before + p["amount_cents"]
    assert client.get("/v1/ledger/trial-balance", headers=OPS).json()["balanced"]
    # blocked receiver can't be paid again
    r = client.post("/v1/payments", headers={**ACME, "Idempotency-Key": "after-r03"},
                    json={"counterparty_id": cp["id"], "amount_cents": 500, "direction": "credit", "sec_code": "PPD",
                          "effective_date": str(_today() + timedelta(days=3)), "purpose": "payroll"})
    assert r.json()["error"]["code"] == "counterparty_blocked"


def test_noc_updates_counterparty(client):
    _run(client)
    p = next(p for p in client.get("/v1/payments", headers=BRIGHT).json() if p["status"] == "settled")
    r = client.post("/v1/sandbox/nocs", headers=OPS, json={"payment_id": p["id"], "change_code": "C01",
                                                            "corrected_data": "99887766"})
    assert r.json()["nocs"] == 1
    cp = client.get(f"/v1/counterparties/{p['counterparty_id']}", headers=BRIGHT).json()
    assert cp["account_last4"] == "7766"
    assert client.get("/v1/nocs", headers=OPS).json()[0]["status"] == "applied"


def test_reversal(client):
    _run(client)
    p = next(p for p in client.get("/v1/payments", headers=ACME).json() if p["status"] == "settled")
    r = client.post(f"/v1/payments/{p['id']}/reversal", headers=ACME, json={"reason": "duplicate"})
    assert r.status_code == 201, r.text
    assert r.json()["direction"] == "debit" and r.json()["company_entry_description"] == "REVERSAL"
    assert client.get(f"/v1/payments/{p['id']}", headers=ACME).json()["status"] == "reversed"
    assert client.post(f"/v1/payments/{p['id']}/reversal", headers=ACME, json={"reason": "duplicate"}).status_code == 409


def test_sanctions_hold_needs_cleared_note(client):
    held = next(p for p in client.get("/v1/payments?status=held", headers=OPS).json()
                if "sanctions" in (p["status_reason"] or "").lower())
    r = client.post(f"/v1/payments/{held['id']}/decision", headers=OPS, json={"decision": "approve", "note": "looks fine"})
    assert r.status_code == 409
    r = client.post(f"/v1/payments/{held['id']}/decision", headers=OPS, json={"decision": "reject", "note": "true match"})
    assert r.json()["status"] == "rejected"
    assert not [a for a in client.get("/v1/alerts", headers=OPS).json() if a["payment_id"] == held["id"]]


def test_changed_account_holds_credit(client):
    cp = _cp(client, ACME, "Daniel Brooks")
    client.put(f"/v1/counterparties/{cp['id']}/account", headers=ACME,
               json={"routing_number": "026009593", "account_number": "11223344"})
    r = client.post("/v1/payments", headers={**ACME, "Idempotency-Key": "div-1"},
                    json={"counterparty_id": cp["id"], "amount_cents": 2_000_00, "direction": "credit", "sec_code": "PPD",
                          "effective_date": str(_today() + timedelta(days=3)), "purpose": "payroll"})
    assert r.json()["status"] == "held"
    detail = client.get(f"/v1/payments/{r.json()['id']}", headers=OPS).json()
    assert any("changed" in reason for s in detail["screening"] for reason in s["reasons"])


def test_rdfi_receipt_paths(client, db):
    entries = [
        {"account_number": "5550001111", "amount_cents": 1_500_00, "individual_name": "Maria Lopez", "company_name": "Payroll Co"},
        {"account_number": "5550004444", "amount_cents": 100_00, "individual_name": "Closed Holder"},
        {"account_number": "0000000009", "amount_cents": 100_00, "individual_name": "Nobody"},
        {"account_number": "7770001111", "amount_cents": 900_00, "direction": "debit", "sec_code": "CCD",
         "company_id": "5555555555", "company_name": "Unknown Biller"},
        {"account_number": "5550003333", "amount_cents": 9_500_00, "individual_name": "Robert Smith",
         "company_name": "Fast Refunds"},
        {"account_number": "5550002222", "amount_cents": 50_00, "individual_name": "James Chen",
         "direction": "debit", "company_name": "Gym", "company_id": "4444444444"},
    ]
    r = client.post("/v1/sandbox/inbound", headers=OPS, json={"entries": entries})
    assert r.status_code == 200 and r.json()["received"] == 6, r.text
    all_entries = client.get("/v1/rdfi/entries", headers=OPS).json()
    got = {e["account_last4"]: e for e in all_entries if e["company_id"] != "5555555555"}
    harbor = next(e for e in all_entries if e["company_id"] == "5555555555")
    assert got["1111"]["status"] == "posted" and got["1111"]["funds_available_at"]
    assert got["4444"]["return_code"] == "R02"
    assert got["0009"]["return_code"] == "R03"
    assert harbor["return_code"] == "R29"
    assert got["3333"]["status"] == "held"   # credit monitoring: name mismatch + new account
    # James Chen's account is savings but entry used a checking code -> auto NOC C05
    assert any(n["change_code"] == "C05" and n["direction"] == "sent" for n in client.get("/v1/nocs", headers=OPS).json())

    # Release the held credit; dispute the gym debit as unauthorized (consumer, WSUD)
    assert client.post(f"/v1/rdfi/entries/{got['3333']['id']}/decision", headers=OPS,
                       json={"decision": "release", "note": "customer confirmed refund"}).json()["status"] == "posted"
    assert client.post(f"/v1/rdfi/entries/{got['2222']['id']}/unauthorized", headers=OPS,
                       json={"return_code": "R10", "wsud_on_file": False}).status_code == 422
    assert client.post(f"/v1/rdfi/entries/{got['2222']['id']}/unauthorized", headers=OPS,
                       json={"return_code": "R10", "wsud_on_file": True}).status_code == 200

    out = _run(client)
    ret = next(f for f in out["files_released"] if f["kind"] == "returns")
    assert ret["entries"] == 5   # R02, R03, R29, R10 + COR (C05)
    raw = client.get(f"/v1/files/{ret['id']}/raw", headers=OPS).text
    assert parse_file(raw).errors == []
    assert client.get("/v1/ledger/trial-balance", headers=OPS).json()["balanced"]
    db.expire_all()
    priya = db.scalar(select(CoreAccount).where(CoreAccount.account_number == "5550003333"))
    assert priya.available_balance_cents == 300_00 + 9_500_00


def test_file_channel(client):
    from app.nacha.file import Batch, Entry, NachaFile
    from datetime import datetime
    nf = NachaFile("011000015", "021000021", "DEMO BANK", "ACME", datetime(2026, 10, 23, 9, 0), batches=[
        Batch("PPD", "ACME PAYROLL", "1111111111", "PAYROLL", _today() + timedelta(days=4), "021000021",
              entries=[Entry("22", "011000015", "4412009876", 1_234_00, "E1", "Daniel Brooks"),
                       Entry("22", "026009593", "5566778899", 987_00, "E2", "New Hire")])])
    r = client.post("/v1/files/originate", headers=ACME, json={"content": nf.render()})
    assert r.status_code == 201, r.text
    assert r.json()["accepted"] == 2
    again = client.post("/v1/files/originate", headers=ACME, json={"content": nf.render()}).json()
    assert all(x.get("replayed") for x in again["results"])


def test_settlement_preview_holiday(client):
    r = client.post("/v1/reference/settlement-preview", headers=ACME,
                    json={"effective_date": "2027-07-05", "sec_code": "PPD", "at": "2027-06-30T12:00:00-04:00"})
    body = r.json()
    assert body["effective_date"] == "2027-07-06" and body["settlement_date"] == "2027-07-06"
    cal = client.get("/v1/reference/calendar?start=2027-12-01&end=2027-12-31", headers=ACME).json()
    assert {d["date"] for d in cal["non_settlement_days"] if d["settles"]} == {"2027-12-24", "2027-12-31"}


def test_calendar_four_eyes(client):
    r = client.post("/v1/calendar/overrides", headers={**OPS, "X-Actor": "alice"},
                    json={"day": "2026-11-27", "kind": "closure", "note": "Emergency closure drill"})
    assert r.status_code == 201
    assert client.post("/v1/calendar/overrides/2026-11-27/approve", headers={**OPS, "X-Actor": "alice"}).status_code == 403
    assert client.post("/v1/calendar/overrides/2026-11-27/approve", headers={**OPS, "X-Actor": "bob"}).status_code == 200
    assert client.get("/v1/reference/business-days?start=2026-11-27&n=0", headers=OPS).json()["is_banking_day"] is False


def test_dashboard_and_reference(client):
    d = client.get("/v1/ops/dashboard", headers=OPS).json()
    assert d["ledger_balanced"] and len(d["windows"]) >= 1
    assert len(client.get("/v1/reference/use-cases", headers=ACME).json()) == 12
    assert client.get("/v1/reference/rules", headers=ACME).json()["dated"][0]["key"] == "same_day_per_entry_limit"
