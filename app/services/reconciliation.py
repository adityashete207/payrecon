"""
Reconciliation engine for PayRecon.

This is the deterministic core of the whole project — pure Python math
and exact-key matching, with ZERO AI involved. Per the PRD's "AI Judgment
Framework": calculations and matching must never be delegated to an LLM.

Tier 1: Exact match — does every Order have a corresponding Gateway
        Transaction (matched by payment_ref)?
Tier 2: Fee/tax variance — for matched pairs, does the gateway's claimed
        net_amount equal what our own math says it SHOULD be
        (Gross - (Gross * MDR) * (1 + GST))? If not, flag it.
"""

from decimal import Decimal

from app.models.schemas import (
    OrderRecord,
    GatewayTransaction,
    ExceptionType,
    MatchStatus,
    ReconException,
)

VARIANCE_TOLERANCE = Decimal("0.01")


def reconcile(
    orders: list[OrderRecord],
    gateway_txns: list[GatewayTransaction],
) -> tuple[list[ReconException], dict]:
    gateway_by_ref = {txn.payment_ref: txn for txn in gateway_txns}

    exceptions: list[ReconException] = []
    matched_count = 0
    exception_counter = 0

    def next_exception_id() -> str:
        nonlocal exception_counter
        exception_counter += 1
        return f"EXC-{exception_counter:04d}"

    for order in orders:
        txn = gateway_by_ref.get(order.payment_ref)

        if txn is None:
            exceptions.append(
                ReconException(
                    exception_id=next_exception_id(),
                    exception_type=ExceptionType.MISSING_GATEWAY_TXN,
                    order_id=order.order_id,
                    payment_ref=order.payment_ref,
                    expected_amount=order.gross_amount,
                    actual_amount=None,
                    variance=None,
                    status=MatchStatus.EXCEPTION,
                )
            )
            continue

        expected_net = txn.expected_net_amount
        actual_net = txn.net_amount
        variance = abs(expected_net - actual_net)

        if variance > VARIANCE_TOLERANCE:
            exceptions.append(
                ReconException(
                    exception_id=next_exception_id(),
                    exception_type=ExceptionType.AMOUNT_MISMATCH,
                    order_id=order.order_id,
                    payment_ref=order.payment_ref,
                    expected_amount=expected_net,
                    actual_amount=actual_net,
                    variance=variance,
                    status=MatchStatus.EXCEPTION,
                )
            )
        else:
            matched_count += 1

    for txn in gateway_txns:
        if txn.payment_ref not in {o.payment_ref for o in orders}:
            exceptions.append(
                ReconException(
                    exception_id=next_exception_id(),
                    exception_type=ExceptionType.UNRECOGNIZED_BANK_ENTRY,
                    order_id=None,
                    payment_ref=txn.payment_ref,
                    expected_amount=None,
                    actual_amount=txn.net_amount,
                    variance=None,
                    status=MatchStatus.EXCEPTION,
                )
            )

    summary = {
        "total_orders": len(orders),
        "matched_count": matched_count,
        "exception_count": len(exceptions),
        "match_rate_pct": round((matched_count / len(orders)) * 100, 2) if orders else 0.0,
    }

    return exceptions, summary