from datetime import date, datetime

from app.nacha.builders import iat_addenda, noc_entry, return_entry
from app.nacha.file import Addenda, Batch, Entry, NachaFile, parse_file, routing_check_digit, valid_routing

ODFI = "021000021"   # valid check digit
RDFI = "011000015"
RDFI2 = "026009593"


def make_file(batches):
    return NachaFile(immediate_destination="011000015", immediate_origin=ODFI, destination_name="FED",
                     origin_name="DEMO BANK", created_at=datetime(2026, 10, 23, 9, 30), batches=batches)


def ppd_batch():
    return Batch(sec_code="PPD", company_name="Acme Payroll", company_id="1234567890",
                 company_entry_description="PAYROLL", effective_entry_date=date(2026, 10, 26), odfi_routing=ODFI,
                 entries=[Entry("22", RDFI, "123456789", 250_000, "EMP001", "Jane Doe"),
                          Entry("32", RDFI2, "987654321", 199_999, "EMP002", "John Roe",
                                addenda=[Addenda("05", {"payment_related_information": "Oct payroll"})])])


def test_routing_check_digit():
    assert valid_routing(ODFI) and valid_routing(RDFI) and valid_routing(RDFI2)
    assert not valid_routing("021000022")
    assert routing_check_digit("02100002") == 1


def test_round_trip_balanced_and_blocked():
    text = make_file([ppd_batch()]).render()
    lines = text.strip().split("\n")
    assert all(len(ln) == 94 for ln in lines)
    assert len(lines) % 10 == 0
    pf = parse_file(text)
    assert pf.errors == []
    assert len(pf.entries) == 2
    assert pf.batches[0]["header"]["service_class_code"] == "220"
    assert pf.control["total_credit"] == "000000449999"
    assert pf.entries[1]["addenda"][0]["payment_related_information"] == "OCT PAYROLL" or \
        pf.entries[1]["addenda"][0]["payment_related_information"] == "Oct payroll"
    assert pf.entries[0]["trace_number"] == "021000020000001"


def test_tampered_amount_detected():
    text = make_file([ppd_batch()]).render()
    lines = text.split("\n")
    i = next(n for n, ln in enumerate(lines) if ln.startswith("6"))
    lines[i] = lines[i][:29] + "0000250001" + lines[i][39:]
    errs = parse_file("\n".join(lines)).errors
    assert any("total credit" in e for e in errs)


def test_mixed_batch_and_ctx():
    ctx = Batch(sec_code="CTX", company_name="Acme Corp", company_id="1234567890", company_entry_description="VENDOR PAY",
                effective_entry_date=date(2026, 10, 26), odfi_routing=ODFI,
                entries=[Entry("22", RDFI, "55555", 10_000_00, "INV-1", "Supplier LLC",
                               addenda=[Addenda("05", {"payment_related_information": f"RMR*IV*{i}*100\\"}) for i in range(3)]),
                         Entry("27", RDFI2, "66666", 5_00, "INV-2", "Customer Inc")])
    pf = parse_file(make_file([ctx]).render())
    assert pf.errors == []
    assert pf.batches[0]["header"]["service_class_code"] == "200"
    assert pf.entries[0]["addenda_count"] == "0003"


def test_iat_round_trip():
    e = Entry("22", RDFI, "GB29NWBK60161331926819", 1_234_56, gateway_ofac_indicator="0",
              addenda=iat_addenda(transaction_type="BUS", amount_cents=1_234_56, receiver_name="UK Supplier Ltd",
                                  originator={"name": "Acme Corp", "street": "1 Main St", "city": "New York", "state": "NY",
                                              "country": "US", "postal": "10001"},
                                  odfi={"name": "Demo Bank", "id": ODFI}, rdfi={"name": "Gateway Bank", "id": RDFI},
                                  receiver={"id": "R1", "street": "10 High St", "city": "London", "state": "",
                                            "country": "GB", "postal": "EC1A1BB"},
                                  remittance=["Invoice 77"]))
    b = Batch(sec_code="IAT", company_name="Acme Corp", company_id="1234567890", company_entry_description="TRADE",
              effective_entry_date=date(2026, 10, 26), odfi_routing=ODFI, entries=[e],
              iat={"iso_destination_country": "GB", "originator_identification": "1234567890"})
    pf = parse_file(make_file([b]).render())
    assert pf.errors == []
    assert [a["addenda_type"] for a in pf.entries[0]["addenda"]] == ["10", "11", "12", "13", "14", "15", "16", "17"]
    assert pf.entries[0]["dfi_account_number"] == "GB29NWBK60161331926819"


def test_iat_missing_addenda_flagged():
    e = Entry("22", RDFI, "123", 100, addenda=[])
    b = Batch(sec_code="IAT", company_name="X", company_id="1", company_entry_description="T",
              effective_entry_date=date(2026, 10, 26), odfi_routing=ODFI, entries=[e])
    assert any("710-716" in err for err in parse_file(make_file([b]).render()).errors)


def test_return_and_noc_entries():
    original = parse_file(make_file([ppd_batch()]).render()).entries[0]
    ret = return_entry(original, ODFI, "R03", RDFI)
    noc = noc_entry(original, ODFI, "C01", "1234567890", RDFI)
    b = Batch(sec_code="PPD", company_name="Acme Payroll", company_id="1234567890", company_entry_description="PAYROLL",
              effective_entry_date=date(2026, 10, 27), odfi_routing=RDFI, entries=[ret, noc])
    pf = parse_file(make_file([b]).render())
    assert pf.errors == []
    r, n = pf.entries
    assert r["transaction_code"] == "21" and r["addenda"][0]["return_reason_code"] == "R03"
    assert r["addenda"][0]["original_trace"] == original["trace_number"]
    assert n["amount"] == "0000000000" and n["addenda"][0]["change_code"] == "C01"
