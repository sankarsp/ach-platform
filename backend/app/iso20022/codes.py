"""Mappings between ISO 20022 codes and Nacha / platform concepts.

ISO external code lists (ExternalCategoryPurpose1Code, ExternalStatusReason1Code,
ExternalReturnReason1Code, Bank Transaction Codes) are published by ISO 20022;
the Nacha↔ISO reason pairs follow the industry convention used for ACH-to-ISO
translation. The original Nacha code is always carried in AddtlInf so nothing
is lost in translation.
"""
from __future__ import annotations

# Category purpose -> (platform purpose, company entry description)
CREDIT_PURPOSE = {
    "SALA": ("payroll", "PAYROLL"),
    "BONU": ("payroll", "PAYROLL"),
    "PENS": ("pension_benefits", "PENSION"),
    "BENE": ("pension_benefits", "BENEFITS"),
    "GOVT": ("pension_benefits", "BENEFITS"),
    "SSBE": ("pension_benefits", "BENEFITS"),
    "SUPP": ("vendor", "VENDOR PAY"),
    "TRAD": ("vendor", "VENDOR PAY"),
    "GDDS": ("vendor", "VENDOR PAY"),
    "TAXS": ("tax", "TAX PMT"),
    "INTC": ("cash_concentration", "SWEEP"),
    "CASH": ("cash_concentration", "SWEEP"),
    "LOAN": ("loan_disbursement", "LOAN"),
    "DIVI": ("other", "DIVIDEND"),
    "REFU": ("refund", "REFUND"),
}
DEBIT_PURPOSE = {
    "LOAN": ("loan_repayment", "LOAN PMT"),
    "LOAR": ("loan_repayment", "LOAN PMT"),
    "TRAD": ("bill_payment", "INVOICE"),
    "SUPP": ("bill_payment", "INVOICE"),
    "TAXS": ("tax", "TAX PMT"),
    "INTC": ("cash_concentration", "SWEEP"),
    "CASH": ("cash_concentration", "SWEEP"),
    "GDSV": ("purchase", "PURCHASE"),
    "UBIL": ("bill_payment", "UTILITY"),
    "ELEC": ("bill_payment", "UTILITY"),
    "INSU": ("bill_payment", "INSURANCE"),
}
BUSINESS_CATEGORIES = {"SUPP", "TRAD", "GDDS", "TAXS", "INTC", "CASH"}

# IAT transaction type code from ISO category purpose
IAT_TYPE = {"SALA": "SAL", "PENS": "PEN", "TAXS": "TAX", "LOAN": "LOA", "SUPP": "BUS", "TRAD": "BUS", "GDDS": "BUS"}

# Nacha return reason -> ISO ExternalReturnReason / StatusReason code
NACHA_TO_ISO = {
    "R01": "AM04", "R02": "AC04", "R03": "AC01", "R04": "AC01", "R05": "MD01", "R06": "FOCR",
    "R07": "MD01", "R08": "MS02", "R09": "AM04", "R10": "MD01", "R11": "MS02", "R16": "AC06",
    "R17": "NARR", "R20": "AG01", "R23": "MS02", "R24": "AM05", "R29": "MD01", "R31": "MS02",
    "R61": "NARR", "R67": "NARR", "R68": "NARR", "R69": "NARR",
}

# Platform validation error -> ISO status reason
DOMAIN_TO_ISO = {
    "per_entry_limit": "AM02", "bad_amount": "AM12", "counterparty_blocked": "AC06", "invalid_routing": "RC01",
    "invalid_account": "AC01", "sec_not_allowed": "AG03", "sec_direction": "AG03", "debits_not_allowed": "AG01",
    "party_mismatch": "AG03", "originator_inactive": "AG01", "entry_description": "NARR", "iat_fields": "BE01",
    "iat_required": "AG03", "too_many_addenda": "RR04", "addenda_length": "RR04", "web_credit": "AG03",
    "bad_sec_code": "AG03", "bad_purpose": "NARR",
}

# NOC change code -> short description (carried in camt.054 additional notification information)
NOC_TEXT = {
    "C01": "Account number corrected", "C02": "Routing number corrected", "C03": "Routing and account corrected",
    "C05": "Account type corrected", "C06": "Account number and type corrected",
    "C07": "Routing, account and type corrected", "C09": "Individual ID corrected",
}

# Platform payment status -> ISO ExternalPaymentTransactionStatus1Code
TX_STATUS = {
    "received": "ACTC", "warehoused": "ACCP", "held": "PDNG", "transmitted": "ACSP", "settled": "ACSC",
    "reversed": "ACSC", "returned": "RJCT", "rejected": "RJCT", "cancelled": "CANC",
}
