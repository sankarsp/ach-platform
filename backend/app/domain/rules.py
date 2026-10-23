"""Versioned Nacha rules catalog.

Every limit, deadline and threshold lives here with its effective date, so a
rule change ships as data plus a regression test rather than a rewrite.
Values reflect the Nacha Operating Rules as understood in Oct 2026 — the
Rules Compliance Officer must confirm them against the current Rules book.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


# ---------------------------------------------------------------- dated values
@dataclass(frozen=True)
class DatedValue:
    key: str
    description: str
    history: tuple[tuple[date, object], ...]  # (effective_date, value), ascending

    def at(self, on: date):
        val = None
        for eff, v in self.history:
            if on >= eff:
                val = v
        if val is None:
            raise LookupError(f"{self.key} not in force on {on}")
        return val


SAME_DAY_LIMIT_CENTS = DatedValue(
    "same_day_per_entry_limit",
    "Same Day ACH per-entry dollar limit",
    ((date(2020, 3, 20), 100_000_00), (date(2022, 3, 18), 1_000_000_00), (date(2027, 9, 17), 10_000_000_00)),
)

NEXT_DAY_CREDIT_AVAILABILITY = DatedValue(
    "next_day_credit_availability",
    "RDFI availability for non-same-day credits (local time on settlement date)",
    ((date(2018, 1, 1), "17:00"), (date(2026, 9, 18), "09:00")),
)

FRAUD_MONITORING_REQUIRED = DatedValue(
    "fraud_monitoring_required",
    "Risk-based fraud monitoring required (Phase 2: all non-consumer Originators, TPS, TPSP, RDFIs)",
    ((date(2000, 1, 1), False), (date(2026, 3, 20), "phase1"), (date(2026, 6, 22), "all")),
)

STANDARD_ENTRY_DESCRIPTIONS = DatedValue(
    "standard_entry_descriptions",
    "Mandatory company entry descriptions",
    ((date(2000, 1, 1), {}), (date(2026, 3, 20), {"payroll": "PAYROLL", "purchase": "PURCHASE"})),
)

RETURN_RATE_THRESHOLDS = DatedValue(
    "return_rate_thresholds",
    "Originator return-rate thresholds (rolling 60 days)",
    ((date(2021, 9, 17), {"unauthorized": 0.005, "administrative": 0.03, "overall": 0.15}),),
)

DATED_RULES = [SAME_DAY_LIMIT_CENTS, NEXT_DAY_CREDIT_AVAILABILITY, FRAUD_MONITORING_REQUIRED,
               STANDARD_ENTRY_DESCRIPTIONS, RETURN_RATE_THRESHOLDS]

UPCOMING_RULE_CALENDAR = [
    {"date": date(2026, 3, 20), "rule": "Fraud monitoring Phase 1; standard entry descriptions PAYROLL and PURCHASE"},
    {"date": date(2026, 6, 22), "rule": "Fraud monitoring and RDFI credit monitoring Phase 2"},
    {"date": date(2026, 9, 18), "rule": "Revised IAT definition; next-day credits available by 9:00 a.m. local"},
    {"date": date(2027, 1, 1), "rule": "IAT contacts in ACH Contact Registry; accepted characters rule"},
    {"date": date(2027, 3, 19), "rule": "IAT formatting changes incl. optional Date of Birth field"},
    {"date": date(2027, 9, 17), "rule": "Same Day ACH per-entry limit rises to $10M"},
    {"date": date(2028, 3, 17), "rule": "Return reason code R90 for sanctions compliance (confirm on nacha.org)"},
]


# ---------------------------------------------------------------- SEC codes
class Party(str, Enum):
    CONSUMER = "consumer"
    BUSINESS = "business"
    EITHER = "either"


@dataclass(frozen=True)
class SecCode:
    code: str
    name: str
    receiver: Party
    credits: bool
    debits: bool
    max_addenda: int
    same_day_eligible: bool
    authorization: str
    unauthorized_return_days: int  # calendar days for consumer codes, banking days handled via R29
    use_cases: tuple[str, ...] = field(default_factory=tuple)


SEC_CODES: dict[str, SecCode] = {s.code: s for s in [
    SecCode("PPD", "Prearranged Payment and Deposit", Party.CONSUMER, True, True, 1, True,
            "Written (signed or similarly authenticated) for debits; agreement for credits", 60,
            ("Payroll", "Pension and benefits", "Refunds", "Automatic bill payment", "Loan disbursement", "Loan repayment")),
    SecCode("CCD", "Corporate Credit or Debit", Party.BUSINESS, True, True, 1, True,
            "Trading-partner agreement", 2,
            ("Vendor payment", "Cash concentration", "Business loan", "Tax payment (TXP)", "Child support (DED)")),
    SecCode("CTX", "Corporate Trade Exchange", Party.BUSINESS, True, True, 9999, True,
            "Trading-partner agreement", 2, ("Invoice-level vendor payment (EDI 820)",)),
    SecCode("WEB", "Internet-Initiated / Mobile Entry", Party.CONSUMER, True, True, 1, True,
            "Online authorization; debits need commercially reasonable account validation", 60,
            ("Online purchase", "Online bill payment", "Person-to-person credit")),
    SecCode("TEL", "Telephone-Initiated Entry", Party.CONSUMER, False, True, 0, True,
            "Recorded oral or written notice", 60, ("Phone-authorized bill payment",)),
    SecCode("IAT", "International ACH Transaction", Party.EITHER, True, True, 12, False,
            "Per underlying SEC type; OFAC screening required", 60, ("Cross-border payroll", "Cross-border supplier payment")),
]}


# ---------------------------------------------------------------- transaction codes
TRANSACTION_CODES = {
    ("checking", "credit"): "22", ("checking", "debit"): "27",
    ("savings", "credit"): "32", ("savings", "debit"): "37",
    ("gl", "credit"): "42", ("gl", "debit"): "47",
    ("loan", "credit"): "52", ("loan", "debit"): "55",
}
PRENOTE_CODES = {"22": "23", "27": "28", "32": "33", "37": "38"}
RETURN_TXN_CODE = {"22": "21", "27": "26", "32": "31", "37": "36", "42": "41", "47": "46", "52": "51", "55": "56"}
CREDIT_CODES = {"21", "22", "23", "24", "31", "32", "33", "34", "41", "42", "43", "51", "52", "53"}


def is_credit(txn_code: str) -> bool:
    return txn_code in CREDIT_CODES


# ---------------------------------------------------------------- return reason codes
class ReturnCategory(str, Enum):
    UNAUTHORIZED = "unauthorized"
    ADMINISTRATIVE = "administrative"
    OTHER = "other"


@dataclass(frozen=True)
class ReturnCode:
    code: str
    description: str
    category: ReturnCategory
    window: int
    window_unit: str  # "banking" | "calendar"
    requires_wsud: bool = False


RETURN_CODES: dict[str, ReturnCode] = {r.code: r for r in [
    ReturnCode("R01", "Insufficient funds", ReturnCategory.OTHER, 2, "banking"),
    ReturnCode("R02", "Account closed", ReturnCategory.ADMINISTRATIVE, 2, "banking"),
    ReturnCode("R03", "No account / unable to locate account", ReturnCategory.ADMINISTRATIVE, 2, "banking"),
    ReturnCode("R04", "Invalid account number structure", ReturnCategory.ADMINISTRATIVE, 2, "banking"),
    ReturnCode("R05", "Unauthorized debit to consumer account using corporate SEC code", ReturnCategory.UNAUTHORIZED, 60, "calendar", True),
    ReturnCode("R06", "Returned per ODFI request", ReturnCategory.OTHER, 60, "calendar"),
    ReturnCode("R07", "Authorization revoked by customer", ReturnCategory.UNAUTHORIZED, 60, "calendar", True),
    ReturnCode("R08", "Payment stopped", ReturnCategory.OTHER, 2, "banking"),
    ReturnCode("R09", "Uncollected funds", ReturnCategory.OTHER, 2, "banking"),
    ReturnCode("R10", "Customer advises originator unknown or entry not authorized", ReturnCategory.UNAUTHORIZED, 60, "calendar", True),
    ReturnCode("R11", "Customer advises entry not in accordance with authorization terms", ReturnCategory.UNAUTHORIZED, 60, "calendar", True),
    ReturnCode("R16", "Account frozen / entry returned per OFAC instruction", ReturnCategory.OTHER, 2, "banking"),
    ReturnCode("R17", "File record edit criteria / questionable entry", ReturnCategory.OTHER, 2, "banking"),
    ReturnCode("R20", "Non-transaction account", ReturnCategory.OTHER, 2, "banking"),
    ReturnCode("R23", "Credit entry refused by receiver", ReturnCategory.OTHER, 2, "banking"),
    ReturnCode("R24", "Duplicate entry", ReturnCategory.OTHER, 2, "banking"),
    ReturnCode("R29", "Corporate customer advises not authorized", ReturnCategory.UNAUTHORIZED, 2, "banking"),
    ReturnCode("R31", "Permissible return entry (CCD/CTX)", ReturnCategory.OTHER, 60, "calendar"),
    ReturnCode("R61", "Misrouted return", ReturnCategory.OTHER, 5, "banking"),
    ReturnCode("R67", "Duplicate return", ReturnCategory.OTHER, 5, "banking"),
    ReturnCode("R68", "Untimely return", ReturnCategory.OTHER, 5, "banking"),
    ReturnCode("R69", "Field error(s)", ReturnCategory.OTHER, 5, "banking"),
]}


# ---------------------------------------------------------------- NOC change codes
NOC_CODES = {
    "C01": ("Incorrect DFI account number", ["account_number"]),
    "C02": ("Incorrect routing number", ["routing_number"]),
    "C03": ("Incorrect routing number and DFI account number", ["routing_number", "account_number"]),
    "C05": ("Incorrect transaction code", ["account_type"]),
    "C06": ("Incorrect DFI account number and transaction code", ["account_number", "account_type"]),
    "C07": ("Incorrect routing number, DFI account number and transaction code", ["routing_number", "account_number", "account_type"]),
    "C09": ("Incorrect individual identification number", ["individual_id"]),
}
NOC_APPLY_BANKING_DAYS = 6

REVERSAL_REASONS = {"duplicate", "wrong_receiver", "wrong_amount", "wrong_date"}
REVERSAL_WINDOW_BANKING_DAYS = 5


def rules_snapshot(on: date) -> dict:
    return {
        "as_of": on.isoformat(),
        "dated": [{"key": r.key, "description": r.description, "value": _jsonable(r.at(on)),
                   "history": [{"effective": e.isoformat(), "value": _jsonable(v)} for e, v in r.history]}
                  for r in DATED_RULES],
        "upcoming": [{"date": u["date"].isoformat(), "rule": u["rule"], "in_force": u["date"] <= on}
                     for u in UPCOMING_RULE_CALENDAR],
    }


def _jsonable(v):
    return v if isinstance(v, (str, int, float, bool, dict, list)) or v is None else str(v)
