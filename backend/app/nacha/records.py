"""Fixed-width Nacha record layouts (94 characters per record).

Each layout is a list of (name, start, end, kind) with 1-based inclusive
positions, as printed in the Nacha Operating Rules appendix. ``kind`` is
"N" (numeric, zero-filled, right-justified) or "A" (alphanumeric,
left-justified, space-filled). Layouts are data, so the builder, the parser
and the validator share one definition.
"""
from __future__ import annotations

RECORD_SIZE = 94
BLOCKING_FACTOR = 10

Field = tuple[str, int, int, str]

FILE_HEADER: list[Field] = [
    ("record_type", 1, 1, "N"), ("priority_code", 2, 3, "N"),
    ("immediate_destination", 4, 13, "A"), ("immediate_origin", 14, 23, "A"),
    ("file_creation_date", 24, 29, "N"), ("file_creation_time", 30, 33, "N"),
    ("file_id_modifier", 34, 34, "A"), ("record_size", 35, 37, "N"), ("blocking_factor", 38, 39, "N"),
    ("format_code", 40, 40, "N"), ("immediate_destination_name", 41, 63, "A"),
    ("immediate_origin_name", 64, 86, "A"), ("reference_code", 87, 94, "A"),
]

BATCH_HEADER: list[Field] = [
    ("record_type", 1, 1, "N"), ("service_class_code", 2, 4, "N"), ("company_name", 5, 20, "A"),
    ("company_discretionary_data", 21, 40, "A"), ("company_identification", 41, 50, "A"),
    ("sec_code", 51, 53, "A"), ("company_entry_description", 54, 63, "A"),
    ("company_descriptive_date", 64, 69, "A"), ("effective_entry_date", 70, 75, "A"),
    ("settlement_date", 76, 78, "A"), ("originator_status_code", 79, 79, "A"),
    ("odfi_identification", 80, 87, "N"), ("batch_number", 88, 94, "N"),
]

IAT_BATCH_HEADER: list[Field] = [
    ("record_type", 1, 1, "N"), ("service_class_code", 2, 4, "N"), ("iat_indicator", 5, 20, "A"),
    ("foreign_exchange_indicator", 21, 22, "A"), ("fx_reference_indicator", 23, 23, "A"),
    ("fx_reference", 24, 38, "A"), ("iso_destination_country", 39, 40, "A"),
    ("originator_identification", 41, 50, "A"), ("sec_code", 51, 53, "A"),
    ("company_entry_description", 54, 63, "A"), ("iso_originating_currency", 64, 66, "A"),
    ("iso_destination_currency", 67, 69, "A"), ("effective_entry_date", 70, 75, "A"),
    ("settlement_date", 76, 78, "A"), ("originator_status_code", 79, 79, "A"),
    ("odfi_identification", 80, 87, "N"), ("batch_number", 88, 94, "N"),
]

ENTRY_DETAIL: list[Field] = [
    ("record_type", 1, 1, "N"), ("transaction_code", 2, 3, "N"), ("rdfi_identification", 4, 11, "N"),
    ("check_digit", 12, 12, "N"), ("dfi_account_number", 13, 29, "A"), ("amount", 30, 39, "N"),
    ("individual_id", 40, 54, "A"), ("individual_name", 55, 76, "A"), ("discretionary_data", 77, 78, "A"),
    ("addenda_indicator", 79, 79, "N"), ("trace_number", 80, 94, "N"),
]

CTX_ENTRY_DETAIL: list[Field] = [
    ("record_type", 1, 1, "N"), ("transaction_code", 2, 3, "N"), ("rdfi_identification", 4, 11, "N"),
    ("check_digit", 12, 12, "N"), ("dfi_account_number", 13, 29, "A"), ("amount", 30, 39, "N"),
    ("individual_id", 40, 54, "A"), ("addenda_count", 55, 58, "N"), ("individual_name", 59, 74, "A"),
    ("reserved", 75, 76, "A"), ("discretionary_data", 77, 78, "A"),
    ("addenda_indicator", 79, 79, "N"), ("trace_number", 80, 94, "N"),
]

IAT_ENTRY_DETAIL: list[Field] = [
    ("record_type", 1, 1, "N"), ("transaction_code", 2, 3, "N"), ("rdfi_identification", 4, 11, "N"),
    ("check_digit", 12, 12, "N"), ("addenda_count", 13, 16, "N"), ("reserved", 17, 29, "A"),
    ("amount", 30, 39, "N"), ("dfi_account_number", 40, 74, "A"), ("reserved2", 75, 76, "A"),
    ("gateway_ofac_indicator", 77, 77, "A"), ("secondary_ofac_indicator", 78, 78, "A"),
    ("addenda_indicator", 79, 79, "N"), ("trace_number", 80, 94, "N"),
]

ADDENDA_05: list[Field] = [
    ("record_type", 1, 1, "N"), ("addenda_type", 2, 3, "N"), ("payment_related_information", 4, 83, "A"),
    ("addenda_sequence", 84, 87, "N"), ("entry_sequence", 88, 94, "N"),
]

ADDENDA_98: list[Field] = [  # Notification of Change
    ("record_type", 1, 1, "N"), ("addenda_type", 2, 3, "N"), ("change_code", 4, 6, "A"),
    ("original_trace", 7, 21, "N"), ("reserved", 22, 27, "A"), ("original_rdfi", 28, 35, "N"),
    ("corrected_data", 36, 64, "A"), ("reserved2", 65, 79, "A"), ("trace_number", 80, 94, "N"),
]

ADDENDA_99: list[Field] = [  # Return
    ("record_type", 1, 1, "N"), ("addenda_type", 2, 3, "N"), ("return_reason_code", 4, 6, "A"),
    ("original_trace", 7, 21, "N"), ("date_of_death", 22, 27, "A"), ("original_rdfi", 28, 35, "N"),
    ("addenda_information", 36, 79, "A"), ("trace_number", 80, 94, "N"),
]

IAT_ADDENDA: dict[str, list[Field]] = {
    "10": [("record_type", 1, 1, "N"), ("addenda_type", 2, 3, "N"), ("transaction_type_code", 4, 6, "A"),
           ("foreign_payment_amount", 7, 24, "N"), ("foreign_trace_number", 25, 46, "A"),
           ("receiver_name", 47, 81, "A"), ("reserved", 82, 87, "A"), ("entry_sequence", 88, 94, "N")],
    "11": [("record_type", 1, 1, "N"), ("addenda_type", 2, 3, "N"), ("originator_name", 4, 38, "A"),
           ("originator_street", 39, 73, "A"), ("reserved", 74, 87, "A"), ("entry_sequence", 88, 94, "N")],
    "12": [("record_type", 1, 1, "N"), ("addenda_type", 2, 3, "N"), ("originator_city_state", 4, 38, "A"),
           ("originator_country_postal", 39, 73, "A"), ("reserved", 74, 87, "A"), ("entry_sequence", 88, 94, "N")],
    "13": [("record_type", 1, 1, "N"), ("addenda_type", 2, 3, "N"), ("odfi_name", 4, 38, "A"),
           ("odfi_id_qualifier", 39, 40, "A"), ("odfi_identification", 41, 74, "A"),
           ("odfi_branch_country", 75, 77, "A"), ("reserved", 78, 87, "A"), ("entry_sequence", 88, 94, "N")],
    "14": [("record_type", 1, 1, "N"), ("addenda_type", 2, 3, "N"), ("rdfi_name", 4, 38, "A"),
           ("rdfi_id_qualifier", 39, 40, "A"), ("rdfi_identification", 41, 74, "A"),
           ("rdfi_branch_country", 75, 77, "A"), ("reserved", 78, 87, "A"), ("entry_sequence", 88, 94, "N")],
    "15": [("record_type", 1, 1, "N"), ("addenda_type", 2, 3, "N"), ("receiver_id_number", 4, 18, "A"),
           ("receiver_street", 19, 53, "A"), ("reserved", 54, 87, "A"), ("entry_sequence", 88, 94, "N")],
    "16": [("record_type", 1, 1, "N"), ("addenda_type", 2, 3, "N"), ("receiver_city_state", 4, 38, "A"),
           ("receiver_country_postal", 39, 73, "A"), ("reserved", 74, 87, "A"), ("entry_sequence", 88, 94, "N")],
    "17": [("record_type", 1, 1, "N"), ("addenda_type", 2, 3, "N"), ("payment_related_information", 4, 83, "A"),
           ("addenda_sequence", 84, 87, "N"), ("entry_sequence", 88, 94, "N")],
}

BATCH_CONTROL: list[Field] = [
    ("record_type", 1, 1, "N"), ("service_class_code", 2, 4, "N"), ("entry_addenda_count", 5, 10, "N"),
    ("entry_hash", 11, 20, "N"), ("total_debit", 21, 32, "N"), ("total_credit", 33, 44, "N"),
    ("company_identification", 45, 54, "A"), ("message_authentication_code", 55, 73, "A"),
    ("reserved", 74, 79, "A"), ("odfi_identification", 80, 87, "N"), ("batch_number", 88, 94, "N"),
]

FILE_CONTROL: list[Field] = [
    ("record_type", 1, 1, "N"), ("batch_count", 2, 7, "N"), ("block_count", 8, 13, "N"),
    ("entry_addenda_count", 14, 21, "N"), ("entry_hash", 22, 31, "N"), ("total_debit", 32, 43, "N"),
    ("total_credit", 44, 55, "N"), ("reserved", 56, 94, "A"),
]

# Characters accepted by the ACH Operators (uppercase alphanumerics and common punctuation).
ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 !\"&'()*+,-./:;<=>?@[\\]^_`{|}~#$%")


class RecordError(ValueError):
    pass


def clean(value: str) -> str:
    return "".join(c if c in ALLOWED else " " for c in (value or ""))


def render(layout: list[Field], values: dict) -> str:
    out = []
    for name, start, end, kind in layout:
        width = end - start + 1
        raw = values.get(name, "")
        if kind == "N":
            s = "" if raw is None or raw == "" else str(int(raw))
            if len(s) > width:
                raise RecordError(f"{name}={raw!r} exceeds {width} digits")
            out.append(s.rjust(width, "0"))
        else:
            s = clean(str(raw if raw is not None else ""))
            out.append(s[:width].ljust(width))
    line = "".join(out)
    assert len(line) == RECORD_SIZE, (layout[0], len(line))
    return line


def parse(layout: list[Field], line: str) -> dict:
    if len(line) != RECORD_SIZE:
        raise RecordError(f"Record length {len(line)} != {RECORD_SIZE}")
    vals = {}
    for name, start, end, kind in layout:
        chunk = line[start - 1:end]
        if kind == "N":
            stripped = chunk.strip()
            if stripped and not stripped.isdigit():
                raise RecordError(f"{name} must be numeric, got {chunk!r}")
            vals[name] = stripped
        else:
            vals[name] = chunk.rstrip()
    return vals
