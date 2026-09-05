"""
Batch data storage for PayRecon.

Every time you upload and reconcile a batch, the raw orders and gateway
transactions now get saved to Postgres as real rows, tagged with a
batch_id. This is what makes Tier 3 batch correlation and later
multi-batch trend analysis possible — without this, each upload would
be an isolated, disconnected snapshot.
"""

import os
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()


def _get_connection():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL not set in .env file")
    return psycopg2.connect(database_url)


def init_data_tables():
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS batches (
            id SERIAL PRIMARY KEY,
            uploaded_at TEXT NOT NULL,
            total_orders INTEGER NOT NULL,
            match_rate_pct NUMERIC NOT NULL,
            exception_count INTEGER NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            batch_id INTEGER REFERENCES batches(id),
            order_id TEXT NOT NULL,
            payment_ref TEXT NOT NULL,
            gross_amount NUMERIC NOT NULL,
            currency TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS gateway_transactions (
            id SERIAL PRIMARY KEY,
            batch_id INTEGER REFERENCES batches(id),
            payment_ref TEXT NOT NULL,
            gross_amount NUMERIC NOT NULL,
            mdr_rate NUMERIC NOT NULL,
            gst_rate NUMERIC NOT NULL,
            net_amount NUMERIC NOT NULL,
            settlement_batch_id TEXT NOT NULL,
            settled_at TEXT NOT NULL
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


def save_batch(orders: list, gateway_txns: list, summary: dict) -> int:
    """
    Persist one reconciliation run: a batch row, plus every order and
    gateway transaction tied to it via batch_id. Returns the new batch_id.
    """
    conn = _get_connection()
    cur = conn.cursor()

    cur.execute(
        """INSERT INTO batches (uploaded_at, total_orders, match_rate_pct, exception_count)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (
            datetime.now(timezone.utc).isoformat(),
            summary["total_orders"],
            summary["match_rate_pct"],
            summary["exception_count"],
        ),
    )
    batch_id = cur.fetchone()[0]

    for order in orders:
        cur.execute(
            """INSERT INTO orders (batch_id, order_id, payment_ref, gross_amount, currency, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (batch_id, order.order_id, order.payment_ref, order.gross_amount, order.currency, order.created_at.isoformat()),
        )

    for txn in gateway_txns:
        cur.execute(
            """INSERT INTO gateway_transactions
               (batch_id, payment_ref, gross_amount, mdr_rate, gst_rate, net_amount, settlement_batch_id, settled_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                batch_id, txn.payment_ref, txn.gross_amount, txn.mdr_rate,
                txn.gst_rate, txn.net_amount, txn.settlement_batch_id, txn.settled_at.isoformat(),
            ),
        )

    conn.commit()
    cur.close()
    conn.close()
    return batch_id


def get_all_batches() -> list[dict]:
    conn = _get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM batches ORDER BY uploaded_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(row) for row in rows]


def get_gateway_transactions_by_batch_ref(settlement_batch_id: str) -> list[dict]:
    """Used by Tier 3: find every individual gateway transaction that
    belongs to a given bank settlement batch reference."""
    conn = _get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT * FROM gateway_transactions WHERE settlement_batch_id = %s",
        (settlement_batch_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(row) for row in rows]