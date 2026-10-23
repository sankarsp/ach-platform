"""ISO 20022: pain.001 / pain.008 intake and pain.002 / camt.054 / camt.053 reporting."""
import xml.etree.ElementTree as ET

from app.iso20022.samples import pain001_payroll, pain001_vendor, pain008_billing

from .conftest import ACME, BRIGHT, NORTH, OPS

XMLH = {"Content-Type": "application/xml"}


def tree(xml: str) -> ET.Element:
    root = ET.fromstring(xml.encode())
    for el in root.iter():
        el.tag = el.tag.split("}", 1)[-1]
    return root


def post(client, kind, headers, xml, **params):
    return client.post(f"/v1/iso20022/{kind}", headers={**headers, **XMLH}, content=xml, params=params)


def test_pain001_payroll_partial_accept(client):
    r = post(client, "pain.001", ACME, pain001_payroll())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["message"]["status"] == "PART"
    st = {x["end_to_end_id"]: x for x in body["results"]}
    assert st["EMP-1001"]["status"] in ("ACCP", "PDNG") and st["EMP-1001"]["payment_id"]
    assert st["EMP-1003"]["status"] == "RJCT" and st["EMP-1003"]["reason"] == "RC01"
    pay = client.get(f"/v1/payments/{st['EMP-1001']['payment_id']}", headers=ACME).json()
    assert pay["sec_code"] == "PPD" and pay["company_entry_description"] == "PAYROLL" and pay["purpose"] == "payroll"

    xml = client.get(f"/v1/iso20022/messages/{body['message']['id']}/pain.002", headers=ACME).text
    t = tree(xml)
    assert t.find("CstmrPmtStsRpt/OrgnlGrpInfAndSts/GrpSts").text == "PART"
    txs = {x.find("OrgnlEndToEndId").text: x for x in t.iter("TxInfAndSts")}
    assert txs["EMP-1003"].find("TxSts").text == "RJCT"
    assert txs["EMP-1003"].find("StsRsnInf/Rsn/Cd").text == "RC01"


def test_duplicate_and_bad_control_sum(client):
    xml = pain001_payroll()
    assert post(client, "pain.001", ACME, xml).status_code == 201
    dup = post(client, "pain.001", ACME, xml)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "duplicate_message"
    bad = pain001_payroll("X").replace("<CtrlSum>6420.00</CtrlSum>", "<CtrlSum>6421.00</CtrlSum>", 1)
    r = post(client, "pain.001", ACME, bad).json()
    assert r["message"]["status"] == "RJCT" and r["message"]["status_reason"].startswith("AM16")
    assert all(x["status"] == "RJCT" and not x["payment_id"] for x in r["results"])


def test_vendor_ctx_and_iat(client):
    r = post(client, "pain.001", NORTH, pain001_vendor())
    assert r.status_code == 201, r.text
    res = {x["end_to_end_id"]: x for x in r.json()["results"]}
    ctx = client.get(f"/v1/payments/{res['VND-77']['payment_id']}", headers=NORTH).json()
    assert ctx["sec_code"] == "CTX" and ctx["addenda"][0].startswith("RMR*IV*INV-20481")
    iat = client.get(f"/v1/payments/{res['VND-78']['payment_id']}", headers=NORTH).json()
    assert iat["sec_code"] == "IAT" and not iat["same_day"] and iat["iat"]["destination_country"] == "GB"


def test_pain008_requires_mandate(client):
    r = post(client, "pain.008", BRIGHT, pain008_billing())
    assert r.status_code == 201, r.text
    res = {x["end_to_end_id"]: x for x in r.json()["results"]}
    assert res["BILL-5003"]["status"] == "RJCT" and res["BILL-5003"]["reason"] == "MD01"
    pay = client.get(f"/v1/payments/{res['BILL-5001']['payment_id']}", headers=OPS).json()
    assert pay["direction"] == "debit" and pay["sec_code"] == "PPD"
    assert pay["transaction_code"] == "37"   # James Chen's savings account (SVGS)
    assert r.json()["message"]["status"] == "PART"


def test_lifecycle_settle_return_and_cash_reports(client):
    body = post(client, "pain.001", ACME, pain001_payroll()).json()
    mid = body["message"]["id"]
    run = client.post("/v1/ops/run", headers=OPS, json={"force": True})
    assert run.status_code == 200
    t = tree(client.get(f"/v1/iso20022/messages/{mid}/pain.002", headers=ACME).text)
    stat = {x.find("OrgnlEndToEndId").text: x.find("TxSts").text for x in t.iter("TxInfAndSts")}
    assert stat["EMP-1001"] == "ACSC" and stat["EMP-1002"] == "ACSC"

    pid = next(x["payment_id"] for x in body["results"] if x["end_to_end_id"] == "EMP-1002")
    assert client.post("/v1/sandbox/returns", headers=OPS, json={"payment_id": pid, "return_code": "R02"}).status_code == 200
    t = tree(client.get(f"/v1/iso20022/messages/{mid}/pain.002", headers=ACME).text)
    tx = next(x for x in t.iter("TxInfAndSts") if x.find("OrgnlEndToEndId").text == "EMP-1002")
    assert tx.find("TxSts").text == "RJCT" and tx.find("StsRsnInf/Rsn/Cd").text == "AC04"
    assert "R02" in tx.find("StsRsnInf/AddtlInf").text

    n = tree(client.get("/v1/iso20022/camt.054", headers=ACME).text)
    entries = list(n.iter("Ntry"))
    assert any(e.find("NtryDtls/TxDtls/Refs/EndToEndId").text == "EMP-1001" and e.find("CdtDbtInd").text == "DBIT"
               for e in entries)
    ret = next(e for e in entries if e.find("RvslInd") is not None)
    assert ret.find("CdtDbtInd").text == "CRDT" and ret.find("NtryDtls/TxDtls/RtrInf/Rsn/Cd").text == "AC04"

    s = tree(client.get("/v1/iso20022/camt.053", headers=ACME).text)
    bal = {b.find("Tp/CdOrPrtry/Cd").text: (b.find("Amt").text, b.find("CdtDbtInd").text) for b in s.iter("Bal")}
    def cents(v): return round(float(v[0]) * 100) * (1 if v[1] == "CRDT" else -1)
    net = 0
    for e in s.iter("Ntry"):
        a = round(float(e.find("Amt").text) * 100)
        net += a if e.find("CdtDbtInd").text == "CRDT" else -a
    assert cents(bal["CLBD"]) - cents(bal["OPBD"]) == net
    accounts = client.get("/v1/rdfi/accounts", headers=OPS).json()
    assert cents(bal["CLBD"]) == next(a["ledger_balance_cents"] for a in accounts if a["account_number"] == "9000000001")


def test_security_and_routing(client):
    xxe = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
           '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.09"><CstmrCdtTrfInitn>&x;</CstmrCdtTrfInitn></Document>')
    r = post(client, "pain.001", ACME, xxe)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_xml"
    assert post(client, "pain.001", BRIGHT, pain008_billing()).json()["error"]["code"] == "wrong_endpoint"
    # Acme cannot debit Northwind's settlement account
    r = post(client, "pain.001", ACME, pain001_vendor()).json()
    assert all(x["status"] == "RJCT" and x["reason"] == "AC02" for x in r["results"])
    # Operators are routed to the originator by the initiating party's company ID
    r = post(client, "pain.001", OPS, pain001_payroll("OP"))
    assert r.status_code == 201
    assert client.get(f"/v1/iso20022/messages/{r.json()['message']['id']}", headers=ACME).status_code == 200
    assert client.get(f"/v1/iso20022/messages/{r.json()['message']['id']}", headers=NORTH).status_code == 404


def test_json_body_and_samples(client):
    names = [s["name"] for s in client.get("/v1/iso20022/samples", headers=ACME).json()]
    assert "pain.008-utility" in names
    xml = client.get("/v1/iso20022/samples/pain.001-payroll", headers=ACME).text
    r = client.post("/v1/iso20022/pain.001", headers=ACME, json={"content": xml})
    assert r.status_code == 201
    lst = client.get("/v1/iso20022/messages", headers=ACME).json()
    assert {m["msg_type"] for m in lst} == {"pain.001.001.09", "pain.002.001.10"}
