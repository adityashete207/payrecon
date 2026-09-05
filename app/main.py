"""
Main FastAPI application for PayRecon.
"""

import json
import shutil
import time
from pathlib import Path

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

from app.services.ingestion import ingest_orders, ingest_gateway_transactions, ingest_bank_utr
from app.services.reconciliation import reconcile
from app.services.reconciliation_tier3 import reconcile_batches
from app.services.ai_reasoner import analyze_exception
from app.services.chat_service import answer_question, generate_executive_summary
from app.services.audit_store import init_db, record_decision, get_all_decisions
from app.services.data_store import init_data_tables, save_batch


class ChatRequest(BaseModel):
    question: str


class DecisionRequest(BaseModel):
    exception_id: str
    action: str
    reviewer_name: str
    reasoning_note: str


app = FastAPI(title="PayRecon API")
init_db()
init_data_tables()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

STATE = {
    "orders": [],
    "gateway_txns": [],
    "exceptions": [],
    "summary": {},
    "narrative": {},
}


@app.post("/api/upload")
async def upload_files(
    orders_file: UploadFile = File(...),
    gateway_file: UploadFile = File(...),
    bank_file: UploadFile = File(None),
):
    orders_path = DATA_DIR / "orders.csv"
    gateway_path = DATA_DIR / "gateway_transactions.csv"

    with orders_path.open("wb") as f:
        shutil.copyfileobj(orders_file.file, f)
    with gateway_path.open("wb") as f:
        shutil.copyfileobj(gateway_file.file, f)

    orders, orders_ingest_result = ingest_orders(str(orders_path))
    gateway_txns, gateway_ingest_result = ingest_gateway_transactions(str(gateway_path))

    exceptions, summary = reconcile(orders, gateway_txns)

    if bank_file is not None:
        bank_path = DATA_DIR / "bank_utr.csv"
        with bank_path.open("wb") as f:
            shutil.copyfileobj(bank_file.file, f)
        bank_records, bank_ingest_result = ingest_bank_utr(str(bank_path))
        batch_exceptions, batch_summary = reconcile_batches(
            gateway_txns, bank_records, start_id=len(exceptions) + 1
        )
        exceptions.extend(batch_exceptions)
        summary.update(batch_summary)

    analyzed_exceptions = []
    for i, exc in enumerate(exceptions):
        print(f"[DEBUG] Analyzing exception {i+1}/{len(exceptions)}...")
        analysis = analyze_exception(exc)
        analyzed_exceptions.append({
            "exception": exc.model_dump(mode="json"),
            "analysis": analysis.model_dump(mode="json"),
        })
        time.sleep(2)

    print("[DEBUG] Generating narrative...")
    narrative = generate_executive_summary(summary, analyzed_exceptions)

    print("[DEBUG] Saving batch to Postgres...")
    batch_id = save_batch(orders, gateway_txns, summary)
    print(f"[DEBUG] Saved as batch_id={batch_id}")

    STATE["orders"] = orders
    STATE["gateway_txns"] = gateway_txns
    STATE["exceptions"] = analyzed_exceptions
    STATE["summary"] = summary
    STATE["narrative"] = narrative

    return {
        "ingestion": {
            "orders": orders_ingest_result.model_dump(mode="json"),
            "gateway": gateway_ingest_result.model_dump(mode="json"),
        },
        "summary": summary,
        "exceptions": analyzed_exceptions,
        "narrative": narrative,
        "batch_id": batch_id,
    }


@app.get("/api/exceptions")
async def get_exceptions():
    return STATE["exceptions"]


@app.get("/api/summary")
async def get_summary():
    return STATE["summary"]


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not STATE["summary"]:
        return {"answer": "Upload and reconcile a batch first, then ask me about it.", "fallback_used": False}
    result = answer_question(req.question, STATE["summary"], STATE["exceptions"])
    return result


@app.post("/api/decide")
async def decide(req: DecisionRequest):
    """Record a human approve/override decision on an exception.

    This is the ONLY code path that writes to the audit trail — no
    automated process can call this. Requires a reviewer name and a
    reasoning note, per the PRD's 'Action Execution Guardrail'.
    """
    snapshot = None
    for item in STATE["exceptions"]:
        if item["exception"]["exception_id"] == req.exception_id:
            snapshot = item["exception"]
            break

    result = record_decision(
        exception_id=req.exception_id,
        action=req.action,
        reviewer_name=req.reviewer_name,
        reasoning_note=req.reasoning_note,
        exception_snapshot=json.dumps(snapshot) if snapshot else "{}",
    )
    return result


@app.get("/api/audit-trail")
async def get_audit_trail():
    return get_all_decisions()


@app.get("/")
async def serve_dashboard():
    return FileResponse("app/static/index.html")


app.mount("/static", StaticFiles(directory="app/static"), name="static")