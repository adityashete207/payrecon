"""
Tier 3: Batch Settlement Correlation for PayRecon.

Real payment gateways don't credit your bank account one transaction at
a time — they bundle many individual payments into ONE aggregate bank
transfer per settlement batch (e.g. "batch_1" might cover 50 separate
customer payments, all wired to your bank in a single UTR).

This tier checks: does the bank's actual credited amount for a batch
match the SUM of what the gateway says it settled for that same batch?
If not, that's a real, hard-to-catch discrepancy — money is missing (or
extra) somewhere in a whole batch, not just one transaction.

Still 100% deterministic — pure summation and comparison, no AI.
"""

from decimal import Decimal
from collections import defaultdict

from app.models.schemas import (
    GatewayTransaction,
    BankUTRRecord,
    ExceptionType,
    MatchStatus,
    ReconException,
)

BATCH_VARIANCE_TOLERANCE = Decimal("0.01")


def reconcile_batches(
    gateway_txns: list[GatewayTransaction],
    bank_utr_records: list[BankUTRRecord],
    start_id: int = 1,
) -> tuple[list[ReconException], dict]:
    """
    Compare each bank settlement batch's actual credited amount against
    the sum of gateway-reported net amounts for that same batch.

    `start_id` lets us continue exception numbering after Tier 1/2's
    exceptions, so IDs stay unique across the whole reconciliation run.
    """
    gateway_totals_by_batch: dict[str, Decimal] = defaultdict(Decimal)
    for txn in gateway_txns:
        gateway_totals_by_batch[txn.settlement_batch_id] += txn.net_amount

    bank_by_batch = {record.settlement_batch_id: record for record in bank_utr_records}

    exceptions: list[ReconException] = []
    matched_batches = 0
    exception_counter = start_id

    def next_id() -> str:
        nonlocal exception_counter
        eid = f"EXC-{exception_counter:04d}"
        exception_counter += 1
        return eid

    all_batch_ids = set(gateway_totals_by_batch.keys()) | set(bank_by_batch.keys())

    for batch_id in sorted(all_batch_ids):
        gateway_total = gateway_totals_by_batch.get(batch_id)
        bank_record = bank_by_batch.get(batch_id)

        if gateway_total is not None and bank_record is None:
            exceptions.append(
                ReconException(
                    exception_id=next_id(),
                    exception_type=ExceptionType.MISSING_BANK_SETTLEMENT,
                    payment_ref=batch_id,
                    expected_amount=gateway_total,
                    actual_amount=None,
                    variance=None,
                    status=MatchStatus.EXCEPTION,
                )
            )
            continue

        if gateway_total is None and bank_record is not None:
            exceptions.append(
                ReconException(
                    exception_id=next_id(),
                    exception_type=ExceptionType.UNRECOGNIZED_BANK_ENTRY,
                    payment_ref=batch_id,
                    expected_amount=None,
                    actual_amount=bank_record.credited_amount,
                    variance=None,
                    status=MatchStatus.EXCEPTION,
                )
            )
            continue

        variance = abs(gateway_total - bank_record.credited_amount)
        if variance > BATCH_VARIANCE_TOLERANCE:
            exceptions.append(
                ReconException(
                    exception_id=next_id(),
                    exception_type=ExceptionType.BATCH_SETTLEMENT_VARIANCE,
                    payment_ref=batch_id,
                    expected_amount=gateway_total,
                    actual_amount=bank_record.credited_amount,
                    variance=variance,
                    status=MatchStatus.EXCEPTION,
                )
            )
        else:
            matched_batches += 1

    summary = {
        "total_batches": len(all_batch_ids),
        "matched_batches": matched_batches,
        "batch_exception_count": len(exceptions),
    }
    return exceptions, summary