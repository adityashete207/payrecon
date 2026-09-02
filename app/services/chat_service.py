"""
AI Chat & Narrative service for PayRecon.

Two AI-forward capabilities live here, both grounded in the deterministic
data we already computed (never inventing numbers):

1. answer_question(): a chat-style Q&A over the current reconciliation
   results. The AI is given the actual summary/exception data as context
   and instructed to answer ONLY from what's provided.

2. generate_executive_summary(): a short narrative paragraph summarizing
   a batch's results.

Same safety pattern as ai_reasoner.py: timeout circuit breaker + graceful
fallback text if the AI call fails.
"""

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from dotenv import load_dotenv

load_dotenv()

CHAT_TIMEOUT_SECONDS = 25
_executor = ThreadPoolExecutor(max_workers=2)


_client = None

def _get_client():
    global _client
    if _client is None:
        from google import genai
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        _client = genai.Client(api_key=api_key)
    return _client


def _call_gemini_text(prompt: str) -> str:
    client = _get_client()
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt,
    )
    return response.text.strip()

def _run_with_timeout(prompt: str, fallback: str) -> tuple[str, bool]:
    """Returns (text, fallback_used)."""
    future = _executor.submit(_call_gemini_text, prompt)
    try:
        return future.result(timeout=CHAT_TIMEOUT_SECONDS), False
    except FutureTimeoutError:
        print(f"[DEBUG] Chat/narrative TIMEOUT after {CHAT_TIMEOUT_SECONDS}s")
        return fallback, True
    except Exception as e:
        print(f"[DEBUG] Chat/narrative error: {e}")
        return fallback, True
    
def answer_question(question: str, summary: dict, exceptions: list[dict]) -> dict:
    context_lines = [
        f"Summary: {summary}",
        "Exceptions:",
    ]
    for item in exceptions:
        exc = item["exception"]
        analysis = item["analysis"]
        context_lines.append(
            f"- {exc['exception_type']} | order={exc.get('order_id')} "
            f"payment_ref={exc.get('payment_ref')} expected={exc.get('expected_amount')} "
            f"actual={exc.get('actual_amount')} variance={exc.get('variance')} "
            f"| AI root cause: {analysis['root_cause']}"
        )
    context = "\n".join(context_lines)

    prompt = f"""You are a financial reconciliation assistant answering questions about
a specific batch of reconciliation results. Answer ONLY using the data below —
never invent numbers or transactions that aren't listed. If the data doesn't
contain the answer, say so plainly. Keep the answer to 2-4 sentences, plain
English, no markdown.

DATA:
{context}

QUESTION: {question}
"""

    fallback = (
        "I couldn't reach the AI service to answer that just now. "
        "You can see the raw exception list and summary numbers above in the meantime."
    )
    answer, fallback_used = _run_with_timeout(prompt, fallback)
    return {"answer": answer, "fallback_used": fallback_used}


def generate_executive_summary(summary: dict, exceptions: list[dict]) -> dict:
    exception_types = [item["exception"]["exception_type"] for item in exceptions]

    prompt = f"""Write a short (2-3 sentence) executive summary of this financial
reconciliation batch, in the plain, direct style a finance controller would
use in a report. Mention the match rate, the number and type of exceptions,
and one notable takeaway. No markdown, no bullet points, just prose.

Summary stats: {summary}
Exception types found: {exception_types}
"""

    fallback = (
        f"This batch processed {summary.get('total_orders', 0)} orders with a "
        f"{summary.get('match_rate_pct', 0)}% match rate. "
        f"{summary.get('exception_count', 0)} exceptions require manual review."
    )
    text, fallback_used = _run_with_timeout(prompt, fallback)
    return {"summary_text": text, "fallback_used": fallback_used}           