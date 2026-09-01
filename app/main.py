"""
Main FastAPI application for PayRecon.

Wires together:
- Ingestion (CSV -> validated Pydantic records)
- Reconciliation (deterministic matching + variance detection)
- AI Reasoner (constrained explanation for each exception, with fallback)

Endpoints:
  POST /api/upload         - upload orders.csv + gateway_transactions.csv
  GET  /api/exceptions      - get all exceptions with AI analysis
  GET  /api/summary         - get match-rate summary stats
  GET  /                    - serve the dashboard HTML
"""

import shutil
from pathlib import Path

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.services.ingestion import ingest_orders, ingest_gateway_transactions
from app.services.reconciliation import reconcile
from app.services.ai_reasoner import analyze_exception

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

    analyzed_exceptions = []
    for exc in exceptions:
        analysis = analyze_exception(exc)
        analyzed_exceptions.append({
            "exception": exc.model_dump(mode="json"),
            "analysis": analysis.model_dump(mode="json"),
        })

    STATE["orders"] = orders
    STATE["gateway_txns"] = gateway_txns
    STATE["exceptions"] = analyzed_exceptions
    STATE["summary"] = summary

    return {
        "ingestion": {
            "orders": orders_ingest_result.model_dump(mode="json"),
            "gateway": gateway_ingest_result.model_dump(mode="json"),
        },
        "summary": summary,
        "exceptions": analyzed_exceptions,
    }


@app.get("/api/exceptions")
async def get_exceptions():
    return STATE["exceptions"]


@app.get("/api/summary")
async def get_summary():
    return STATE["summary"]


@app.get("/")
async def serve_dashboard():
    return FileResponse("app/static/index.html")


app.mount("/static", StaticFiles(directory="app/static"), name="static")