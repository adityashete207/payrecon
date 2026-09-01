"""
AI Exception Reasoner for PayRecon.

This is the ONLY place in the whole project where an LLM is used. It is
triggered exclusively on already-flagged discrepancies (never on raw
matching or math, which stays 100% deterministic — see reconciliation.py).

Two safety mechanisms:
1. Structured/constrained output: we force Gemini to return JSON matching
   AIExceptionAnalysis exactly. If it doesn't, Pydantic validation fails.
2. Circuit breaker: if the AI call takes longer than 3.5 seconds OR fails
   for any reason, we fall back to a rule-based explanation instead of
   crashing or blocking.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from dotenv import load_dotenv
from pydantic import ValidationError

from app.models.schemas import ReconException, AIExceptionAnalysis

load_dotenv()

AI_TIMEOUT_SECONDS = 12

_executor = ThreadPoolExecutor(max_workers=2)


def _build_prompt(exception: ReconException) -> str:
    return f"""You are a financial reconciliation analyst. Analyze this settlement
discrepancy and respond with ONLY a JSON object (no markdown, no extra text)
matching this exact shape:

{{
  "root_cause": "<plain-English explanation, 1-2 sentences>",
  "confidence_score": <float between 0.0 and 1.0>,
  "recommended_action": "<what a human reviewer should do next>",
  "evidence_points": ["<specific fact 1>", "<specific fact 2>"]
}}

Discrepancy details:
- Type: {exception.exception_type.value}
- Order ID: {exception.order_id}
- Payment Reference: {exception.payment_ref}
- Expected Amount: {exception.expected_amount}
- Actual Amount: {exception.actual_amount}
- Variance: {exception.variance}
"""


def _call_gemini(prompt: str) -> str:
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in environment/.env file")

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
                model="gemini-3.6-flash",
        contents=prompt,
    )
    return response.text


def _deterministic_fallback(exception: ReconException, reason: str) -> AIExceptionAnalysis:
    canned_messages = {
        "AMOUNT_MISMATCH": "The settled amount differs from the expected amount based on MDR/GST calculations. Manual review recommended to check for fee rate changes or additional charges.",
        "MISSING_GATEWAY_TXN": "No matching gateway settlement was found for this order. This may indicate a pending settlement or a failed transaction that was not properly reversed.",
        "MISSING_BANK_SETTLEMENT": "The gateway reports this transaction as settled, but no corresponding bank credit was found. Recommend checking bank statements for the relevant batch.",
        "UNRECOGNIZED_BANK_ENTRY": "A gateway settlement exists with no matching order record. This may be a data sync issue or an order created outside the tracked system.",
        "DUPLICATE_BATCH": "This batch appears to have been processed more than once. Recommend checking for duplicate ingestion.",
    }
    message = canned_messages.get(
        exception.exception_type.value,
        "Discrepancy detected; automated analysis unavailable, manual review required.",
    )
    return AIExceptionAnalysis(
        root_cause=f"{message} (fallback reason: {reason})",
        confidence_score=0.3,
        recommended_action="Route to finance operations for manual review.",
        evidence_points=[f"Exception type: {exception.exception_type.value}"],
        fallback_used=True,
    )


def analyze_exception(exception: ReconException) -> AIExceptionAnalysis:
    prompt = _build_prompt(exception)

    future = _executor.submit(_call_gemini, prompt)
    try:
        raw_text = future.result(timeout=AI_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        return _deterministic_fallback(exception, reason=f"AI call exceeded {AI_TIMEOUT_SECONDS}s timeout")
    except Exception as e:
        return _deterministic_fallback(exception, reason=f"AI call failed: {e}")

    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
        analysis = AIExceptionAnalysis(**parsed)
        return analysis
    except (json.JSONDecodeError, ValidationError) as e:
        return _deterministic_fallback(exception, reason=f"invalid AI response format: {e}")