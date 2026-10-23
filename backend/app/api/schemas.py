from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ originators
class OriginatorIn(BaseModel):
    name: str = Field(max_length=120)
    company_id: str = Field(pattern=r"^[0-9A-Z]{10}$", description="10-character Nacha company identification")
    allowed_sec_codes: list[str] = ["PPD", "CCD"]
    allows_debits: bool = False
    same_day_enabled: bool = True
    daily_limit_cents: int = Field(1_000_000_00, gt=0)
    per_entry_limit_cents: int = Field(250_000_00, gt=0)
    risk_rating: Literal["low", "medium", "high"] = "medium"
    is_third_party_sender: bool = False
    settlement_account_number: str | None = None
    kyb_status: Literal["pending", "approved", "rejected"] = "pending"
    agreement_signed_at: datetime | None = None
    address: dict = {}


class OriginatorPatch(BaseModel):
    allowed_sec_codes: list[str] | None = None
    allows_debits: bool | None = None
    same_day_enabled: bool | None = None
    daily_limit_cents: int | None = Field(None, gt=0)
    per_entry_limit_cents: int | None = Field(None, gt=0)
    risk_rating: Literal["low", "medium", "high"] | None = None
    status: Literal["active", "suspended"] | None = None
    kyb_status: Literal["pending", "approved", "rejected"] | None = None
    agreement_signed_at: datetime | None = None


class OriginatorOut(ORM):
    id: str
    name: str
    company_id: str
    status: str
    kyb_status: str
    risk_rating: str
    is_third_party_sender: bool
    allowed_sec_codes: list[str]
    allows_debits: bool
    same_day_enabled: bool
    daily_limit_cents: int
    per_entry_limit_cents: int
    settlement_account_number: str | None
    agreement_signed_at: datetime | None
    created_at: datetime


# ------------------------------------------------------------------ counterparties
class CounterpartyIn(BaseModel):
    originator_id: str | None = None
    name: str = Field(max_length=120)
    party_type: Literal["consumer", "business"] = "consumer"
    routing_number: str = Field(min_length=8, max_length=34)
    account_number: str = Field(min_length=1, max_length=35)
    account_type: Literal["checking", "savings", "gl", "loan"] = "checking"
    individual_id: str = Field("", max_length=15)
    country: str = Field("US", min_length=2, max_length=2)
    address: dict = {}
    validate_with: Literal["instant", "prenote", "none"] = "instant"


class AccountChangeIn(BaseModel):
    routing_number: str
    account_number: str


class CounterpartyOut(ORM):
    id: str
    originator_id: str
    name: str
    party_type: str
    routing_number: str
    account_last4: str
    account_type: str
    country: str
    validation_status: str
    validation_method: str | None
    status: str
    block_reason: str | None
    account_changed_at: datetime | None
    first_paid_at: datetime | None
    created_at: datetime


# ------------------------------------------------------------------ payments
class IatIn(BaseModel):
    transaction_type: str = Field(description="e.g. BUS, SAL, ANN, PEN, MIS")
    destination_country: str
    destination_currency: str = "USD"
    originator: dict = {}
    receiver: dict
    rdfi: dict


class PaymentIn(BaseModel):
    originator_id: str | None = None
    counterparty_id: str
    amount_cents: int = Field(gt=0)
    direction: Literal["credit", "debit"]
    sec_code: Literal["PPD", "CCD", "CTX", "WEB", "TEL", "IAT"]
    effective_date: date
    purpose: str = "other"
    company_entry_description: str | None = Field(None, max_length=10)
    same_day: bool = False
    addenda: list[str] = []
    web_payment_type: Literal["R", "S"] | None = None
    iat: IatIn | None = None
    metadata: dict = {}


class PaymentOut(ORM):
    id: str
    originator_id: str
    counterparty_id: str
    direction: str
    sec_code: str
    purpose: str
    amount_cents: int
    company_entry_description: str
    transaction_code: str
    requested_effective_date: date
    effective_date: date
    same_day_requested: bool
    same_day: bool
    window_code: str | None
    window_cutoff_at: datetime | None
    expected_settlement_at: datetime | None
    settlement_date: date | None
    status: str
    status_reason: str | None
    plan_notes: list
    risk_score: int
    trace_number: str | None
    file_id: str | None
    return_code: str | None
    reversal_of_id: str | None
    created_at: datetime
    transmitted_at: datetime | None
    settled_at: datetime | None
    returned_at: datetime | None


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = ""


class ReversalIn(BaseModel):
    reason: Literal["duplicate", "wrong_receiver", "wrong_amount", "wrong_date"]


class PreviewIn(BaseModel):
    effective_date: date
    sec_code: str = "PPD"
    amount_cents: int = 100_00
    same_day: bool = False
    at: datetime | None = None


# ------------------------------------------------------------------ RDFI
class InboundDecisionIn(BaseModel):
    decision: Literal["release", "return"]
    note: str = ""


class UnauthorizedClaimIn(BaseModel):
    return_code: Literal["R05", "R07", "R10", "R11", "R29"]
    wsud_on_file: bool = False


class NocIn(BaseModel):
    change_code: str
    corrected_data: str = Field(max_length=29)


class CoreAccountIn(BaseModel):
    account_number: str
    owner_name: str
    owner_type: Literal["consumer", "business"] = "consumer"
    account_type: Literal["checking", "savings"] = "checking"
    balance_cents: int = 0
    debit_block: bool = False
    allowed_debit_company_ids: list[str] = []


class CoreAccountPatch(BaseModel):
    status: Literal["open", "closed", "frozen"] | None = None
    debit_block: bool | None = None
    allowed_debit_company_ids: list[str] | None = None


# ------------------------------------------------------------------ ops
class AlertUpdateIn(BaseModel):
    status: Literal["open", "escalated", "closed"] | None = None
    assignee: str | None = None
    disposition: Literal["no_action", "escalated_to_case", "false_positive", "true_positive"] | None = None
    note: str = ""
    case_id: str | None = None


class CaseIn(BaseModel):
    title: str
    subject: str = ""
    alert_ids: list[str] = []


class CaseUpdateIn(BaseModel):
    note: str = ""
    action: Literal["note", "file_sar", "close"] = "note"
    sar_reference: str | None = None


class IncidentUpdateIn(BaseModel):
    action: Literal["acknowledge", "resolve"]
    note: str = ""


class RunIn(BaseModel):
    force: bool = Field(False, description="Sandbox only: release and settle immediately, ignoring cut-offs")
    at: datetime | None = Field(None, description="Sandbox only: run as if at this time")


class CalendarOverrideIn(BaseModel):
    day: date
    kind: Literal["closure", "opening"]
    note: str


class WebhookIn(BaseModel):
    originator_id: str | None = None
    url: str
    events: list[str] = ["*"]


class SimReturnIn(BaseModel):
    payment_id: str
    return_code: str


class SimNocIn(BaseModel):
    payment_id: str
    change_code: str
    corrected_data: str


class SimInboundItem(BaseModel):
    account_number: str
    amount_cents: int = Field(ge=0)
    direction: Literal["credit", "debit"] = "credit"
    sec_code: Literal["PPD", "CCD", "WEB", "TEL"] = "PPD"
    company_name: str = "Outside Co"
    company_id: str = "9876543210"
    entry_description: str = "PAYMENT"
    individual_name: str = ""
    account_type: Literal["checking", "savings"] = "checking"
    same_day_window: Literal["SD1", "SD2", "SD3"] | None = None


class SimInboundIn(BaseModel):
    entries: list[SimInboundItem]


class FileIn(BaseModel):
    content: str = Field(description="Raw Nacha file text")
