"""Quick test to find which OpenRouter free models actually work."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openai import OpenAI
import config

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=config.OPENROUTER_API_KEY)

models_to_test = [
    "google/gemma-3-12b-it:free",
    "google/gemma-3-27b-it:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "qwen/qwen3-4b:free",
    "qwen/qwen3-coder:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
]

for m in models_to_test:
    try:
        r = client.chat.completions.create(
            model=m,
            messages=[{"role": "user", "content": "Say hello in exactly 5 words."}],
            max_tokens=30,
        )
        text = r.choices[0].message.content
        print(f"  OK  {m} -> {text}")
    except Exception as e:
        err = str(e)[:100]
        print(f"  FAIL {m} -> {err}")
