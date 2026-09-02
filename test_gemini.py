import os
import time
from dotenv import load_dotenv
from google import genai

load_dotenv()

api_key = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

for i in range(3):
    print(f"--- Attempt {i+1} ---")
    start = time.time()
    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents="Say hello in one sentence.",
        )
        elapsed = time.time() - start
        print(f"SUCCESS in {elapsed:.1f}s: {response.text}")
    except Exception as e:
        elapsed = time.time() - start
        print(f"FAILED after {elapsed:.1f}s: {e}")
    print()