"""Higher-level builders: IAT addenda sets, return entries and NOC entries."""
from __future__ import annotations

from .file import Addenda, Entry, yymmdd
from ..domain.rules import RETURN_TXN_CODE


def iat_addenda(*, transaction_type: str, amount_cents: int, receiver_name: str,
                originator: dict, odfi: dict, rdfi: dict, receiver: dict,
                remittance: list[str] | None = None) -> list[Addenda]:
    """The seven mandatory IAT addenda (710-716) plus optional 717 remittance.

    originator/receiver: name, street, city, state, country, postal, id
    odfi/rdfi: name, id_qualifier ("01" national clearing, "02" BIC, "03" IBAN), id, country
    """
    out = [
        Addenda("10", {"transaction_type_code": transaction_type, "foreign_payment_amount": amount_cents,
                       "receiver_name": receiver_name}),
        Addenda("11", {"originator_name": originator["name"], "originator_street": originator.get("street", "")}),
        Addenda("12", {"originator_city_state": f"{originator.get('city', '')}*{originator.get('state', '')}\\",
                       "originator_country_postal": f"{originator.get('country', 'US')}*{originator.get('postal', '')}\\"}),
        Addenda("13", {"odfi_name": odfi["name"], "odfi_id_qualifier": odfi.get("id_qualifier", "01"),
                       "odfi_identification": odfi["id"], "odfi_branch_country": odfi.get("country", "US")}),
        Addenda("14", {"rdfi_name": rdfi["name"], "rdfi_id_qualifier": rdfi.get("id_qualifier", "01"),
                       "rdfi_identification": rdfi["id"], "rdfi_branch_country": rdfi.get("country", "US")}),
        Addenda("15", {"receiver_id_number": receiver.get("id", ""), "receiver_street": receiver.get("street", "")}),
        Addenda("16", {"receiver_city_state": f"{receiver.get('city', '')}*{receiver.get('state', '')}\\",
                       "receiver_country_postal": f"{receiver.get('country', '')}*{receiver.get('postal', '')}\\"}),
    ]
    for text in (remittance or [])[:2]:
        out.append(Addenda("17", {"payment_related_information": text}))
    return out


def return_entry(original: dict, original_odfi_routing: str, reason_code: str, rdfi_routing: str,
                 addenda_info: str = "", date_of_death=None) -> Entry:
    """Entry an RDFI sends back to the ODFI for a received entry (original = parsed entry dict)."""
    return Entry(
        transaction_code=RETURN_TXN_CODE[original["transaction_code"]],
        rdfi_routing=original_odfi_routing,
        account_number=original["dfi_account_number"],
        amount_cents=int(original["amount"]),
        individual_id=original.get("individual_id", ""),
        individual_name=original.get("individual_name", ""),
        addenda=[Addenda("99", {"return_reason_code": reason_code, "original_trace": original["trace_number"],
                                "date_of_death": yymmdd(date_of_death), "original_rdfi": rdfi_routing[:8],
                                "addenda_information": addenda_info})],
    )


def noc_entry(original: dict, original_odfi_routing: str, change_code: str, corrected_data: str,
              rdfi_routing: str) -> Entry:
    """Zero-dollar COR entry carrying a Notification of Change."""
    return Entry(
        transaction_code=RETURN_TXN_CODE[original["transaction_code"]],
        rdfi_routing=original_odfi_routing,
        account_number=original["dfi_account_number"],
        amount_cents=0,
        individual_id=original.get("individual_id", ""),
        individual_name=original.get("individual_name", ""),
        addenda=[Addenda("98", {"change_code": change_code, "original_trace": original["trace_number"],
                                "original_rdfi": rdfi_routing[:8], "corrected_data": corrected_data})],
    )
