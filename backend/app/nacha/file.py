"""Build and parse Nacha files.

``NachaFile.render()`` produces a balanced, blocked file; ``parse_file()``
reads one back and ``validate()`` re-checks counts, hashes, totals, blocking
and routing check digits. Covers PPD, CCD, CTX, WEB, TEL, IAT, return (99)
and Notification of Change (98) entries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from . import records as R
from ..domain.rules import is_credit


# ------------------------------------------------------------------ helpers
def routing_check_digit(first8: str) -> int:
    weights = (3, 7, 1, 3, 7, 1, 3, 7)
    total = sum(int(d) * w for d, w in zip(first8, weights))
    return (10 - total % 10) % 10


def valid_routing(rtn: str) -> bool:
    return len(rtn) == 9 and rtn.isdigit() and routing_check_digit(rtn[:8]) == int(rtn[8])


def yymmdd(d: date | None) -> str:
    return d.strftime("%y%m%d") if d else ""


def from_yymmdd(s: str) -> date | None:
    s = (s or "").strip()
    return datetime.strptime(s, "%y%m%d").date() if len(s) == 6 and s.isdigit() else None


# ------------------------------------------------------------------ model
@dataclass
class Addenda:
    type_code: str                      # "05", "98", "99" or IAT "10".."17"
    fields: dict = field(default_factory=dict)


@dataclass
class Entry:
    transaction_code: str
    rdfi_routing: str                   # 9 digits
    account_number: str
    amount_cents: int
    individual_id: str = ""
    individual_name: str = ""
    discretionary_data: str = ""        # WEB: payment type code "R"/"S"
    trace_number: str = ""              # assigned by the batch if blank
    addenda: list[Addenda] = field(default_factory=list)
    # IAT only
    gateway_ofac_indicator: str = ""
    secondary_ofac_indicator: str = ""

    @property
    def is_credit(self) -> bool:
        return is_credit(self.transaction_code)


@dataclass
class Batch:
    sec_code: str
    company_name: str
    company_id: str
    company_entry_description: str
    effective_entry_date: date
    odfi_routing: str
    entries: list[Entry] = field(default_factory=list)
    company_discretionary_data: str = ""
    company_descriptive_date: str = ""
    batch_number: int = 1
    settlement_date_julian: str = ""
    # IAT batch header fields
    iat: dict = field(default_factory=dict)

    @property
    def odfi8(self) -> str:
        return self.odfi_routing[:8]

    @property
    def service_class_code(self) -> str:
        credits = any(e.is_credit for e in self.entries)
        debits = any(not e.is_credit for e in self.entries)
        return "200" if credits and debits else ("220" if credits else "225")

    def assign_traces(self, start_seq: int) -> int:
        seq = start_seq
        for e in self.entries:
            if not e.trace_number:
                e.trace_number = f"{self.odfi8}{seq:07d}"
            seq += 1
        return seq


@dataclass
class NachaFile:
    immediate_destination: str          # operator / receiving point routing (9)
    immediate_origin: str               # ODFI routing or company id (9-10)
    destination_name: str
    origin_name: str
    created_at: datetime
    file_id_modifier: str = "A"
    reference_code: str = ""
    batches: list[Batch] = field(default_factory=list)

    # ---------------------------------------------------------------- render
    def render(self) -> str:
        lines: list[str] = []
        lines.append(R.render(R.FILE_HEADER, {
            "record_type": 1, "priority_code": 1,
            "immediate_destination": " " + self.immediate_destination.strip()[-9:],
            "immediate_origin": self.immediate_origin.strip().rjust(10),
            "file_creation_date": self.created_at.strftime("%y%m%d"),
            "file_creation_time": self.created_at.strftime("%H%M"),
            "file_id_modifier": self.file_id_modifier, "record_size": 94, "blocking_factor": 10,
            "format_code": 1, "immediate_destination_name": self.destination_name.upper(),
            "immediate_origin_name": self.origin_name.upper(), "reference_code": self.reference_code,
        }))
        f_count = f_hash = f_debit = f_credit = 0
        seq = 1
        for i, b in enumerate(self.batches, start=1):
            b.batch_number = b.batch_number if b.batch_number and b.batch_number >= i else i
            seq = b.assign_traces(seq)
            lines.append(_render_batch_header(b))
            count = h = debit = credit = 0
            for e in b.entries:
                lines.append(_render_entry(b, e))
                count += 1 + len(e.addenda)
                h += int(e.rdfi_routing[:8])
                if e.is_credit:
                    credit += e.amount_cents
                else:
                    debit += e.amount_cents
                for n, a in enumerate(e.addenda, start=1):
                    lines.append(_render_addenda(e, a, n))
            lines.append(R.render(R.BATCH_CONTROL, {
                "record_type": 8, "service_class_code": b.service_class_code, "entry_addenda_count": count,
                "entry_hash": h % 10**10, "total_debit": debit, "total_credit": credit,
                "company_identification": b.company_id if b.sec_code != "IAT" else b.iat.get("originator_identification", b.company_id),
                "odfi_identification": b.odfi8, "batch_number": b.batch_number,
            }))
            f_count += count
            f_hash += h
            f_debit += debit
            f_credit += credit
        records_so_far = len(lines) + 1
        blocks = -(-records_so_far // R.BLOCKING_FACTOR)
        lines.append(R.render(R.FILE_CONTROL, {
            "record_type": 9, "batch_count": len(self.batches), "block_count": blocks,
            "entry_addenda_count": f_count, "entry_hash": f_hash % 10**10,
            "total_debit": f_debit, "total_credit": f_credit,
        }))
        while len(lines) % R.BLOCKING_FACTOR:
            lines.append("9" * R.RECORD_SIZE)
        return "\n".join(lines) + "\n"


def _render_batch_header(b: Batch) -> str:
    if b.sec_code == "IAT":
        i = b.iat
        return R.render(R.IAT_BATCH_HEADER, {
            "record_type": 5, "service_class_code": b.service_class_code,
            "iat_indicator": i.get("iat_indicator", ""),
            "foreign_exchange_indicator": i.get("foreign_exchange_indicator", "FF"),
            "fx_reference_indicator": i.get("fx_reference_indicator", "3"),
            "fx_reference": i.get("fx_reference", ""),
            "iso_destination_country": i.get("iso_destination_country", "US"),
            "originator_identification": i.get("originator_identification", b.company_id),
            "sec_code": "IAT", "company_entry_description": b.company_entry_description.upper(),
            "iso_originating_currency": i.get("iso_originating_currency", "USD"),
            "iso_destination_currency": i.get("iso_destination_currency", "USD"),
            "effective_entry_date": yymmdd(b.effective_entry_date), "settlement_date": b.settlement_date_julian,
            "originator_status_code": "1", "odfi_identification": b.odfi8, "batch_number": b.batch_number,
        })
    return R.render(R.BATCH_HEADER, {
        "record_type": 5, "service_class_code": b.service_class_code, "company_name": b.company_name.upper(),
        "company_discretionary_data": b.company_discretionary_data, "company_identification": b.company_id,
        "sec_code": b.sec_code, "company_entry_description": b.company_entry_description.upper(),
        "company_descriptive_date": b.company_descriptive_date,
        "effective_entry_date": yymmdd(b.effective_entry_date), "settlement_date": b.settlement_date_julian,
        "originator_status_code": "1", "odfi_identification": b.odfi8, "batch_number": b.batch_number,
    })


def _render_entry(b: Batch, e: Entry) -> str:
    base = {"record_type": 6, "transaction_code": e.transaction_code, "rdfi_identification": e.rdfi_routing[:8],
            "check_digit": e.rdfi_routing[8], "amount": e.amount_cents,
            "addenda_indicator": 1 if e.addenda else 0, "trace_number": e.trace_number}
    if b.sec_code == "IAT":
        return R.render(R.IAT_ENTRY_DETAIL, {**base, "addenda_count": len(e.addenda),
                                             "dfi_account_number": e.account_number,
                                             "gateway_ofac_indicator": e.gateway_ofac_indicator,
                                             "secondary_ofac_indicator": e.secondary_ofac_indicator})
    if b.sec_code == "CTX":
        return R.render(R.CTX_ENTRY_DETAIL, {**base, "dfi_account_number": e.account_number,
                                             "individual_id": e.individual_id, "addenda_count": len(e.addenda),
                                             "individual_name": e.individual_name.upper(),
                                             "discretionary_data": e.discretionary_data})
    return R.render(R.ENTRY_DETAIL, {**base, "dfi_account_number": e.account_number,
                                     "individual_id": e.individual_id, "individual_name": e.individual_name.upper(),
                                     "discretionary_data": e.discretionary_data})


def _render_addenda(e: Entry, a: Addenda, n: int) -> str:
    seq7 = e.trace_number[-7:]
    if a.type_code == "05":
        return R.render(R.ADDENDA_05, {"record_type": 7, "addenda_type": 5, "addenda_sequence": n,
                                       "entry_sequence": seq7, **a.fields})
    if a.type_code == "98":
        return R.render(R.ADDENDA_98, {"record_type": 7, "addenda_type": 98,
                                       "trace_number": e.trace_number, **a.fields})
    if a.type_code == "99":
        return R.render(R.ADDENDA_99, {"record_type": 7, "addenda_type": 99,
                                       "trace_number": e.trace_number, **a.fields})
    layout = R.IAT_ADDENDA[a.type_code]
    extra = {"addenda_sequence": n - 7} if a.type_code == "17" else {}
    return R.render(layout, {"record_type": 7, "addenda_type": int(a.type_code), "entry_sequence": seq7,
                             **extra, **a.fields})


# ------------------------------------------------------------------ parse
@dataclass
class ParsedFile:
    header: dict
    batches: list[dict]
    control: dict
    raw_records: int
    errors: list[str]

    @property
    def entries(self) -> list[dict]:
        return [e for b in self.batches for e in b["entries"]]


def parse_file(text: str) -> ParsedFile:
    lines = [ln.rstrip("\r") for ln in text.splitlines() if ln.strip()]
    errors: list[str] = []
    header: dict = {}
    control: dict = {}
    batches: list[dict] = []
    cur: dict | None = None
    last_entry: dict | None = None
    for n, ln in enumerate(lines, start=1):
        if len(ln) != R.RECORD_SIZE:
            errors.append(f"Line {n}: length {len(ln)} (expected 94)")
            continue
        t = ln[0]
        try:
            if t == "1":
                header = R.parse(R.FILE_HEADER, ln)
            elif t == "5":
                sec = ln[50:53]
                hdr = R.parse(R.IAT_BATCH_HEADER if sec == "IAT" else R.BATCH_HEADER, ln)
                cur = {"header": hdr, "sec_code": sec, "entries": [], "control": None}
                batches.append(cur)
            elif t == "6":
                if cur is None:
                    errors.append(f"Line {n}: entry outside a batch")
                    continue
                layout = {"IAT": R.IAT_ENTRY_DETAIL, "CTX": R.CTX_ENTRY_DETAIL}.get(cur["sec_code"], R.ENTRY_DETAIL)
                last_entry = R.parse(layout, ln)
                last_entry["addenda"] = []
                cur["entries"].append(last_entry)
            elif t == "7":
                if last_entry is None:
                    errors.append(f"Line {n}: addenda without an entry")
                    continue
                at = ln[1:3]
                if at == "05":
                    a = R.parse(R.ADDENDA_05, ln)
                elif at == "98":
                    a = R.parse(R.ADDENDA_98, ln)
                elif at == "99":
                    a = R.parse(R.ADDENDA_99, ln)
                elif at in R.IAT_ADDENDA:
                    a = R.parse(R.IAT_ADDENDA[at], ln)
                else:
                    errors.append(f"Line {n}: unknown addenda type {at}")
                    continue
                last_entry["addenda"].append(a)
            elif t == "8":
                if cur is None:
                    errors.append(f"Line {n}: batch control without header")
                    continue
                cur["control"] = R.parse(R.BATCH_CONTROL, ln)
                cur, last_entry = None, None
            elif t == "9":
                if ln == "9" * R.RECORD_SIZE:
                    continue
                control = R.parse(R.FILE_CONTROL, ln)
            else:
                errors.append(f"Line {n}: unknown record type {t!r}")
        except R.RecordError as exc:
            errors.append(f"Line {n}: {exc}")
    pf = ParsedFile(header, batches, control, len(lines), errors)
    pf.errors.extend(validate(pf))
    return pf


def validate(pf: ParsedFile) -> list[str]:
    errs: list[str] = []
    if not pf.header:
        errs.append("Missing file header")
    if not pf.control:
        errs.append("Missing file control")
        return errs
    if pf.raw_records % R.BLOCKING_FACTOR:
        errs.append(f"File not blocked in multiples of 10 ({pf.raw_records} records)")
    f_count = f_hash = f_debit = f_credit = 0
    for i, b in enumerate(pf.batches, start=1):
        c = b["control"]
        if c is None:
            errs.append(f"Batch {i}: missing batch control")
            continue
        count = sum(1 + len(e["addenda"]) for e in b["entries"])
        h = sum(int(e["rdfi_identification"]) for e in b["entries"]) % 10**10
        debit = sum(int(e["amount"]) for e in b["entries"] if not is_credit(e["transaction_code"]))
        credit = sum(int(e["amount"]) for e in b["entries"] if is_credit(e["transaction_code"]))
        for name, got, want in (("entry/addenda count", count, int(c["entry_addenda_count"])),
                                ("entry hash", h, int(c["entry_hash"])),
                                ("total debit", debit, int(c["total_debit"])),
                                ("total credit", credit, int(c["total_credit"]))):
            if got != want:
                errs.append(f"Batch {i}: {name} {got} != control {want}")
        for e in b["entries"]:
            rtn = e["rdfi_identification"] + e["check_digit"]
            if not valid_routing(rtn):
                errs.append(f"Batch {i}: trace {e['trace_number']} routing {rtn} fails check digit")
            ind = int(e["addenda_indicator"] or 0)
            if ind != (1 if e["addenda"] else 0):
                errs.append(f"Batch {i}: trace {e['trace_number']} addenda indicator mismatch")
        if b["sec_code"] == "IAT":
            for e in b["entries"]:
                types = [a["addenda_type"] for a in e["addenda"]]
                if types[:7] != ["10", "11", "12", "13", "14", "15", "16"]:
                    errs.append(f"Batch {i}: IAT trace {e['trace_number']} missing mandatory addenda 710-716")
        f_count += count
        f_hash += h
        f_debit += debit
        f_credit += credit
    c = pf.control
    for name, got, want in (("batch count", len(pf.batches), int(c["batch_count"])),
                            ("entry/addenda count", f_count, int(c["entry_addenda_count"])),
                            ("entry hash", f_hash % 10**10, int(c["entry_hash"])),
                            ("total debit", f_debit, int(c["total_debit"])),
                            ("total credit", f_credit, int(c["total_credit"]))):
        if got != want:
            errs.append(f"File: {name} {got} != control {want}")
    return errs
