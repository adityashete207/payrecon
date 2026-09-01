"""
Pydantic schemas for PayRecon.

WHY THIS FILE MATTERS:
Every piece of data that enters the system (an order, a gateway settlement
row, a bank UTR row) gets validated against one of these models BEFORE any
reconciliation logic touches it. If a CSV row has a bad timestamp, a
non-numeric amount, or a missing field, Pydantic raises a validation error
immediately instead of letting bad data silently corrupt matching logic
downstream.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------

class ExceptionType(str, Enum):
    """The catalog of discrepancy types Tier 2/3 matching can produce."""
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    MISSING_BANK_SETTLEMENT = "MISSING_BANK_SETTLEMENT"
    MISSING_GATEWAY_TXN = "MISSING_GATEWAY_TXN"
    UNRECOGNIZED_BANK_ENTRY = "UNRECOGNIZED_BANK_ENTRY"
    DUPLICATE_BATCH = "DUPLICATE_BATCH"


class MatchStatus(str, Enum):
    MATCHED = "MATCHED"
    EXCEPTION = "EXCEPTION"
    PENDING_REVIEW = "PENDING_REVIEW"
    RESOLVED = "RESOLVED"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_ref(value: str) -> str:
    """Trim whitespace and uppercase a reference ID for consistent matching."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("reference id must be a non-empty string")
    return value.strip().upper()


def _to_decimal(value) -> Decimal:
    """Safely convert incoming amount fields to Decimal (never float, for money)."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"'{value}' is not a valid decimal amount")


# ---------------------------------------------------------------------------
# Source 1: Internal Order
# ---------------------------------------------------------------------------

class OrderRecord(BaseModel):
    order_id: str = Field(..., description="Merchant's internal order identifier")
    payment_ref: str = Field(..., description="Gateway payment ID linked to this order")
    gross_amount: Decimal = Field(..., description="Full amount charged to the customer")
    currency: str = Field(default="INR")
    created_at: datetime

    @field_validator("order_id", "payment_ref")
    @classmethod
    def clean_ids(cls, v: str) -> str:
        return _clean_ref(v)

    @field_validator("gross_amount", mode="before")
    @classmethod
    def validate_amount(cls, v):
        amount = _to_decimal(v)
        if amount <= 0:
            raise ValueError("gross_amount must be positive")
        return amount


# ---------------------------------------------------------------------------
# Source 2: Payment Gateway Settlement Row
# ---------------------------------------------------------------------------

class GatewayTransaction(BaseModel):
    payment_ref: str = Field(..., description="Gateway payment ID (matches OrderRecord.payment_ref)")
    gross_amount: Decimal
    mdr_rate: Decimal = Field(..., description="Merchant Discount Rate, e.g. 0.02 for 2%")
    gst_rate: Decimal = Field(default=Decimal("0.18"), description="GST on the MDR fee")
    net_amount: Decimal = Field(..., description="Amount gateway claims it settled, post-fees")
    settlement_batch_id: str = Field(..., description="Batch this txn was rolled into")
    settled_at: datetime

    @field_validator("payment_ref", "settlement_batch_id")
    @classmethod
    def clean_ids(cls, v: str) -> str:
        return _clean_ref(v)

    @field_validator("gross_amount", "net_amount", "mdr_rate", "gst_rate", mode="before")
    @classmethod
    def validate_amounts(cls, v):
        return _to_decimal(v)

    @property
    def expected_net_amount(self) -> Decimal:
        """Deterministic fee math — NEVER delegated to the AI layer.

        Expected Net = Gross - (Gross * MDR) * (1 + GST)
        """
        fee = (self.gross_amount * self.mdr_rate) * (Decimal("1") + self.gst_rate)
        return self.gross_amount - fee


# ---------------------------------------------------------------------------
# Source 3: Bank UTR Settlement Feed
# ---------------------------------------------------------------------------

class BankUTRRecord(BaseModel):
    utr_number: str = Field(..., description="Unique Transaction Reference from the bank")
    settlement_batch_id: str = Field(..., description="Correlates to GatewayTransaction.settlement_batch_id")
    credited_amount: Decimal
    value_date: datetime

    @field_validator("utr_number", "settlement_batch_id")
    @classmethod
    def clean_ids(cls, v: str) -> str:
        return _clean_ref(v)

    @field_validator("credited_amount", mode="before")
    @classmethod
    def validate_amount(cls, v):
        amount = _to_decimal(v)
        if amount <= 0:
            raise ValueError("credited_amount must be positive")
        return amount


# ---------------------------------------------------------------------------
# Ingestion error reporting (Failure Mode 3: dirty/malformed input)
# ---------------------------------------------------------------------------

class RowValidationError(BaseModel):
    """One row that failed validation during ingestion — collected instead
    of crashing the whole batch on one bad row."""
    row_number: int
    raw_data: dict
    error_message: str


class IngestionResult(BaseModel):
    source: str
    total_rows: int
    valid_rows: int
    errors: list[RowValidationError] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Reconciliation Exception (what gets shown in the dashboard / sent to AI)
# ---------------------------------------------------------------------------

class ReconException(BaseModel):
    exception_id: str
    exception_type: ExceptionType
    order_id: Optional[str] = None
    payment_ref: Optional[str] = None
    expected_amount: Optional[Decimal] = None
    actual_amount: Optional[Decimal] = None
    variance: Optional[Decimal] = None
    status: MatchStatus = MatchStatus.EXCEPTION
    detected_at: datetime = Field(default_factory=datetime.utcnow)