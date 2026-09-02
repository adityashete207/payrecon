"""
Main FastAPI application for PayRecon.

Wires together:
- Ingestion (CSV -> validated Pydantic records)
- Reconciliation (deterministic matching + variance detection)
- AI Reasoner (constrained explanation for each exception, with fallback)
- AI Chat/Narrative (Q&A and executive summaries over batch results)

Endpoints:
  POST /api/upload    - upload orders.csv + gateway_transactions.csv
  GET  /api/exceptions - get all exceptions with AI analysis
  GET  /api/summary    - get match-rate summary stats
  POST /api/chat        - ask a natural-language question about the batch
  GET  /                - serve the dashboard HTML
"""

import shutil
from pathlib import Path

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

from app.services.ingestion import ingest_orders, ingest_gateway_transactions
from app.services.reconciliation import reconcile
from app.services.ai_reasoner import analyze_exception
from app.services.chat_service import answer_question, generate_executive_summary


class ChatRequest(BaseModel):
    question: str


app = FastAPI(title="PayRecon API")

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

    import time
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


@app.get("/")
async def serve_dashboard():
    return FileResponse("app/static/index.html")


app.mount("/static", StaticFiles(directory="app/static"), name="static")