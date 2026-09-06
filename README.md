# PayRecon — AI-Assisted Financial Reconciliation & Exception Management

Built for the **Razorpay AI Buildathon 2026** — AI Finance Controller track.

## The problem

Digital merchants process transactions across three disconnected systems: internal order records, payment gateway settlement dumps, and bank UTR feeds. Reconciling these manually — accounting for MDR fees, GST, settlement batching, and timing delays — is slow, error-prone, and hides real revenue leakage. Finance teams typically do this in spreadsheets, batch by batch, by hand.

## What PayRecon does

PayRecon automates 3-way reconciliation across all three data sources, flags discrepancies with mathematical precision, and uses AI *only* where AI actually adds value: explaining *why* a discrepancy likely happened, summarizing a batch in plain English, and answering questions about the results — never for the money math itself.

## The core design decision: where AI is and isn't used

This is the single most important idea in the project. Financial systems can't tolerate AI hallucinating numbers, so PayRecon draws a hard line:

| Task | Engine | Why |
|---|---|---|
| Transaction matching | Deterministic (exact key match) | Zero tolerance for ambiguity in identity matching |
| Fee/tax math (MDR, GST) | Deterministic (`Decimal` arithmetic) | Money math must never be probabilistic |
| Batch/bank correlation | Deterministic (summation + comparison) | Same reasoning — aggregate totals must be exact |
| *Why* a discrepancy happened | AI (Gemini, schema-constrained) | This is genuinely ambiguous — a good fit for reasoning |
| Batch executive summaries | AI | Plain-English narrative, not a numeric claim |
| Natural-language Q&A | AI, grounded in the batch's actual data | Explicitly labeled `FROM YOUR DATA` vs `GENERAL KNOWLEDGE` so it's never ambiguous which one you're getting |
| Approving/overriding an exception | Human only | No automated code path can write to the audit trail |

## Architecture

┌─────────────┐ ┌──────────────────┐ ┌─────────────────────┐
│ CSV/UI │────▶│ FastAPI backend │────▶│ Postgres (Neon) │
│ (uploads) │ │ │ │ batches/orders/ │
└─────────────┘ │ - Ingestion │ │ gateway_txns/ │
│ - Tier 1: exact │ │ audit_log │
│ match │ └─────────────────────┘
│ - Tier 2: fee/ │
│ tax variance │ ┌─────────────────────┐
│ - Tier 3: batch/ │────▶│ Gemini API │
│ bank corr. │ │ (constrained JSON, │
│ - AI reasoner │ │ circuit breaker, │
│ - AI chat/ │ │ fallback) │
│ narrative │ └─────────────────────┘
└──────────────────┘

**Stack**: Python, FastAPI, Pydantic v2 (strict schema validation), Postgres via Neon (cloud-hosted), Google Gemini API (`gemini-3.5-flash-lite`), vanilla HTML/JS dashboard.

## Failure modes handled (and demonstrated live)

Per the evaluation criterion "what broke and how you fixed it":

1. **LLM timeout / outage** — every AI call runs inside a hard timeout with a deterministic, rule-based fallback. This was hit for real during development (a model was deprecated mid-build, and the free-tier quota ran out) — the fallback fired exactly as designed both times, with no crash.
2. **Malformed AI output** — AI responses are validated against a strict Pydantic schema; anything that doesn't fit is treated as a failure and routed to the same fallback.
3. **Dirty/malformed input CSVs** — bad rows (non-numeric amounts, missing IDs, negative values) are rejected individually with line-level diagnostics, never crashing the whole batch. Visible live in the dashboard's "Data Quality" panel.
4. **Duplicate/mismatched batches** — Tier 3 batch correlation catches cases where a bank's aggregate credit doesn't match the sum of what the gateway reported for that batch — a discrepancy invisible to transaction-level checks alone.

## Running it locally

1. Clone the repo and `cd` into it.
2. `pip install -r requirements.txt` (or install packages individually — see below).
3. Create a `.env` file with:
    GEMINI_API_KEY=your_key_here
    DATABASE_URL=your_postgres_connection_string
4. `python -m uvicorn app.main:app --reload`
5. Open `http://127.0.0.1:8000`, upload the sample CSVs from `/data`, and run reconciliation.

## What's built vs. designed-but-not-built

Being upfront: this is a working prototype demonstrating the full architecture end-to-end, not a production system. Things deliberately kept simple for the Buildathon timeline:
- Authentication on approve/override actions is name-based (typed in), not a full login system.
- The dashboard is a single-page vanilla JS app, not a full React SPA — deliberate, to keep the surface area small and every line auditable.
- Multi-batch trend analysis (e.g. detecting a rising variance pattern over weeks) is a natural next step, now that batch history is persisted in Postgres.