"""
Audit trail storage for PayRecon — backed by Postgres (via Neon).

Same public functions as the original SQLite version (init_db,
record_decision, get_all_decisions), so nothing else in the app needed
to change — only how storage works underneath. This is exactly why we
split the app into services: swapping the database didn't touch the
API layer or the dashboard at all.
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


def init_db():
    """Create the audit_log table if it doesn't already exist."""
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id SERIAL PRIMARY KEY,
            exception_id TEXT NOT NULL,
            action TEXT NOT NULL,
            reviewer_name TEXT NOT NULL,
            reasoning_note TEXT NOT NULL,
            exception_snapshot TEXT NOT NULL,
            decided_at TEXT NOT NULL
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


def record_decision(exception_id: str, action: str, reviewer_name: str, reasoning_note: str, exception_snapshot: str) -> dict:
    conn = _get_connection()
    cur = conn.cursor()
    decided_at = datetime.now(timezone.utc).isoformat()
    cur.execute(
        """INSERT INTO audit_log (exception_id, action, reviewer_name, reasoning_note, exception_snapshot, decided_at)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (exception_id, action, reviewer_name, reasoning_note, exception_snapshot, decided_at),
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return {
        "id": new_id,
        "exception_id": exception_id,
        "action": action,
        "reviewer_name": reviewer_name,
        "reasoning_note": reasoning_note,
        "decided_at": decided_at,
    }


def get_all_decisions() -> list[dict]:
    conn = _get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM audit_log ORDER BY decided_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(row) for row in rows]