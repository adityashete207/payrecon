"""
Ingestion service for PayRecon.

Reads a raw CSV file and converts each row into a validated Pydantic
model (OrderRecord, GatewayTransaction, or BankUTRRecord).

Key behavior: if one row is bad (e.g. a typo'd amount, a missing ID),
we do NOT crash the whole file. We skip that row, record exactly what
went wrong, and keep processing the rest. This is "Failure Mode 3"
from the PRD: dirty/malformed input files get logged, not crashed on.
"""

import csv
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from app.models.schemas import (
    OrderRecord,
    GatewayTransaction,
    BankUTRRecord,
    RowValidationError,
    IngestionResult,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


def ingest_csv(file_path: str, model: Type[ModelT], source_name: str) -> tuple[list[ModelT], IngestionResult]:
    """
    Read a CSV file, validate every row against `model`.

    Returns:
        (valid_records, result_summary)
        - valid_records: list of successfully parsed Pydantic objects
        - result_summary: IngestionResult with counts and any row errors
    """
    valid_records: list[ModelT] = []
    errors: list[RowValidationError] = []
    total_rows = 0

    with open(file_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row_number, raw_row in enumerate(reader, start=1):
            total_rows += 1
            try:
                record = model(**raw_row)
                valid_records.append(record)
            except ValidationError as e:
                errors.append(
                    RowValidationError(
                        row_number=row_number,
                        raw_data=raw_row,
                        error_message=str(e),
                    )
                )

    result = IngestionResult(
        source=source_name,
        total_rows=total_rows,
        valid_rows=len(valid_records),
        errors=errors,
    )
    return valid_records, result


def ingest_orders(file_path: str) -> tuple[list[OrderRecord], IngestionResult]:
    return ingest_csv(file_path, OrderRecord, source_name="orders")


def ingest_gateway_transactions(file_path: str) -> tuple[list[GatewayTransaction], IngestionResult]:
    return ingest_csv(file_path, GatewayTransaction, source_name="gateway_transactions")


def ingest_bank_utr(file_path: str) -> tuple[list[BankUTRRecord], IngestionResult]:
    return ingest_csv(file_path, BankUTRRecord, source_name="bank_utr")