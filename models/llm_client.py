"""
Unified LLM Client — Routes calls to Groq, OpenRouter, or Google Gemini.

Features:
- Per-call logging with [PROVIDER] [MODEL] [ROLE] → status
- Clear error attribution (rate-limit, auth, server error)
- Automatic retries with exponential backoff
- Fallback routing to alternate models
- Output validation against expected format
"""

import time
import json
import logging
import os
import re
from datetime import datetime
from openai import OpenAI

# Setup logging
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# ─────────────────────────── LOGGER SETUP ───────────────────────
logger = logging.getLogger("AEVE")
logger.setLevel(logging.DEBUG)

# Console handler (colored output)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_fmt = logging.Formatter("%(asctime)s │ %(levelname)-7s │ %(message)s", datefmt="%H:%M:%S")
console_handler.setFormatter(console_fmt)
logger.addHandler(console_handler)

# File handler (full debug log)
log_file = os.path.join(config.LOG_DIR, "pipeline.log")
file_handler = logging.FileHandler(log_file, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_fmt = logging.Formatter("%(asctime)s │ %(levelname)-7s │ %(message)s")
file_handler.setFormatter(file_fmt)
logger.addHandler(file_handler)


# ─────────────────────────── ERROR CLASSES ──────────────────────
class LLMError(Exception):
    """Rich error with full context about which model/provider/role failed."""
    def __init__(self, role, provider, model, status_code, error_body, error_type):
        self.role = role
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.error_body = error_body
        self.error_type = error_type  # "rate_limit", "auth", "server", "unknown"
        super().__init__(self._format())

    def _format(self):
        return (
            f"\n{'='*60}\n"
            f"  LLM CALL FAILED\n"
            f"  Role:       {self.role}\n"
            f"  Provider:   {self.provider}\n"
            f"  Model:      {self.model}\n"
            f"  Error Type: {self.error_type}\n"
            f"  HTTP Code:  {self.status_code}\n"
            f"  Details:    {self.error_body[:500]}\n"
            f"{'='*60}"
        )


class OutputValidationError(Exception):
    """Raised when model output does not match expected format."""
    def __init__(self, role, expected, got_preview):
        self.role = role
        self.expected = expected
        self.got_preview = got_preview
        super().__init__(
            f"\n{'='*60}\n"
            f"  OUTPUT VALIDATION FAILED\n"
            f"  Role:     {role}\n"
            f"  Expected: {expected}\n"
            f"  Got:      {got_preview[:300]}...\n"
            f"{'='*60}"
        )


# ─────────────────────────── VALIDATION HELPERS ─────────────────
def validate_output(role: str, text: str, expected_format: str = None) -> str:
    """
    Validate that the model's output matches what we asked for.

    expected_format can be:
      - "json"          → must be parseable JSON
      - "json_array"    → must be a JSON array [...]
      - "python_code"   → must contain 'class' or 'def' and look like Python
      - "latex_rich"    → must contain at least one LaTeX expression ($...$)
      - "text"          → any non-empty text (default)
      - None            → skip validation
    """
    if text is None or text.strip() == "":
        raise OutputValidationError(role, expected_format or "non-empty text", "(empty response)")

    text = text.strip()

    if expected_format == "json":
        # Extract JSON from markdown code blocks if present
        json_match = re.search(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
        if json_match:
            text = json_match.group(1)
        try:
            json.loads(text)
        except json.JSONDecodeError as e:
            raise OutputValidationError(role, "valid JSON", f"JSON parse error: {e}\n{text[:200]}")

    elif expected_format == "json_array":
        json_match = re.search(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
        if json_match:
            text = json_match.group(1)
        try:
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                raise OutputValidationError(role, "JSON array [...]", f"Got type: {type(parsed).__name__}")
        except json.JSONDecodeError as e:
            raise OutputValidationError(role, "valid JSON array", f"JSON parse error: {e}\n{text[:200]}")

    elif expected_format == "python_code":
        # Extract code from markdown blocks if present
        code_match = re.search(r'```(?:python)?\s*\n(.*?)\n```', text, re.DOTALL)
        if code_match:
            text = code_match.group(1)
        if "class " not in text and "def " not in text and "from manim" not in text:
            raise OutputValidationError(role, "Python code with class/def", text[:200])

    elif expected_format == "latex_rich":
        if "$" not in text:
            raise OutputValidationError(role, "text containing LaTeX ($...$)", text[:200])

    elif expected_format == "text":
        if len(text.strip()) < 10:
            raise OutputValidationError(role, "substantive text (>10 chars)", text)

    logger.info(f"  ✓ Output validation passed for {role} (format: {expected_format or 'any'})")
    return text


# ─────────────────────────── PROVIDER CLIENTS ───────────────────
def _classify_error(status_code: int) -> str:
    if status_code == 429:
        return "rate_limit"
    elif status_code in (401, 403):
        return "auth"
    elif status_code >= 500:
        return "server"
    return "unknown"


# ─── Groq Key Rotation State ────────────────────────────────────
_groq_key_index = 0

def _get_groq_key() -> str:
    """Get the current Groq API key."""
    return config.GROQ_API_KEYS[_groq_key_index % len(config.GROQ_API_KEYS)]

def _rotate_groq_key() -> str:
    """Rotate to the next Groq API key. Returns the new key."""
    global _groq_key_index
    _groq_key_index = (_groq_key_index + 1) % len(config.GROQ_API_KEYS)
    new_key = config.GROQ_API_KEYS[_groq_key_index]
    key_num = _groq_key_index + 1
    total = len(config.GROQ_API_KEYS)
    logger.info(f"  🔄 Rotated to Groq key {key_num}/{total} (***{new_key[-6:]})")
    return new_key

def _call_groq(model: str, system_prompt: str, user_prompt: str) -> str:
    """Call Groq API (OpenAI-compatible) using current rotated key."""
    api_key = _get_groq_key()
    client = OpenAI(api_key=api_key, base_url=config.GROQ_BASE_URL)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=8000,
    )
    return response.choices[0].message.content


def _call_openrouter(model: str, system_prompt: str, user_prompt: str) -> str:
    """Call OpenRouter API (OpenAI-compatible)."""
    client = OpenAI(api_key=config.OPENROUTER_API_KEY, base_url=config.OPENROUTER_BASE_URL)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=8000,
    )
    content = response.choices[0].message.content
    # DeepSeek R1 wraps reasoning in <think> tags — extract the final answer
    if "<think>" in content and "</think>" in content:
        # Get everything after the last </think> tag
        parts = content.split("</think>")
        content = parts[-1].strip()
    return content


def _call_gemini(model: str, system_prompt: str, user_prompt: str, image_path: str = None) -> str:
    """Call Google Gemini API."""
    from google import genai
    client = genai.Client(api_key=config.GOOGLE_API_KEY)

    contents = []
    if image_path and os.path.exists(image_path):
        # Upload image for multimodal input
        with open(image_path, "rb") as f:
            import base64
            image_data = base64.b64encode(f.read()).decode()

        from google.genai import types
        contents.append(
            types.Content(
                role="user",
                parts=[
                    types.Part(text=system_prompt + "\n\n" + user_prompt),
                    types.Part(
                        inline_data=types.Blob(
                            mime_type="image/png",
                            data=base64.b64decode(image_data),
                        )
                    ),
                ],
            )
        )
    else:
        from google.genai import types
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part(text=system_prompt + "\n\n" + user_prompt)],
            )
        )

    response = client.models.generate_content(
        model=model,
        contents=contents,
    )
    return response.text


# ─────────────────────────── MAIN DISPATCHER ────────────────────
def call_model(
    role: str,
    user_prompt: str,
    expected_format: str = "text",
    image_path: str = None,
    system_prompt_extra: str = "",
    context_injection: str = "",
) -> str:
    """
    Call the appropriate model for a given role with full logging and validation.

    Args:
        role:                 One of M1-M10
        user_prompt:          The main prompt to send
        expected_format:      "json", "json_array", "python_code", "latex_rich", "text"
        image_path:           Optional image for multimodal (M1 only)
        system_prompt_extra:  Additional system instructions beyond LaTeX enforcement
        context_injection:    Extra context (e.g., Script 1 for M9)

    Returns:
        Validated model output as a string
    """
    route = config.MODEL_ROUTING[role]
    provider = route["provider"]
    model = route["model"]
    fallback_model = route["fallback_model"]
    role_desc = route["description"]

    # Build system prompt with all context the model needs
    system_prompt = (
        f"You are acting as: {role} — {role_desc}\n\n"
        f"{config.LATEX_SYSTEM_PROMPT}\n"
        f"{system_prompt_extra}\n"
    ).strip()

    # Inject context if provided
    full_user_prompt = user_prompt
    if context_injection:
        full_user_prompt = (
            f"=== REFERENCE CONTEXT (Deep Solution) ===\n{context_injection}\n"
            f"=== END CONTEXT ===\n\n{user_prompt}"
        )

    # Try primary model, then fallback
    models_to_try = [(model, "primary"), (fallback_model, "fallback")]

    for current_model, model_tier in models_to_try:
        for attempt in range(1, config.MAX_RETRIES + 1):
            start_time = time.time()
            try:
                logger.info(
                    f"[{provider.upper()}] [{current_model}] [{role}] "
                    f"→ Sending request (attempt {attempt}/{config.MAX_RETRIES}, {model_tier})..."
                )
                logger.debug(f"  System prompt length: {len(system_prompt)} chars")
                logger.debug(f"  User prompt length:   {len(full_user_prompt)} chars")

                # Dispatch to correct provider
                if provider == "groq":
                    raw_response = _call_groq(current_model, system_prompt, full_user_prompt)
                elif provider == "openrouter":
                    raw_response = _call_openrouter(current_model, system_prompt, full_user_prompt)
                elif provider == "google":
                    raw_response = _call_gemini(current_model, system_prompt, full_user_prompt, image_path)
                else:
                    raise ValueError(f"Unknown provider: {provider}")

                elapsed = time.time() - start_time
                response_len = len(raw_response) if raw_response else 0
                logger.info(
                    f"[{provider.upper()}] [{current_model}] [{role}] "
                    f"→ ✓ Response received ({response_len} chars, {elapsed:.1f}s)"
                )

                # Validate output matches expectations
                validated = validate_output(role, raw_response, expected_format)

                # Log first 200 chars of response for debugging
                logger.debug(f"  Response preview: {validated[:200]}...")

                return validated

            except OutputValidationError:
                logger.warning(f"  ⚠ Output validation failed for {role} (attempt {attempt})")
                if attempt == config.MAX_RETRIES and model_tier == "fallback":
                    raise
                # Retry — the model might give a better response
                time.sleep(config.RETRY_BACKOFF_BASE ** attempt)

            except Exception as e:
                elapsed = time.time() - start_time
                error_str = str(e)

                # Try to extract HTTP status code
                status_code = 0
                if hasattr(e, "status_code"):
                    status_code = e.status_code
                elif "429" in error_str:
                    status_code = 429
                elif "401" in error_str or "403" in error_str:
                    status_code = 401
                elif "500" in error_str or "502" in error_str or "503" in error_str:
                    status_code = 500

                error_type = _classify_error(status_code)

                logger.error(
                    f"[{provider.upper()}] [{current_model}] [{role}] "
                    f"→ ✗ FAILED (attempt {attempt}, {error_type}, {elapsed:.1f}s): {error_str[:200]}"
                )

                # On Groq rate limit → rotate to next key and retry immediately
                if provider == "groq" and status_code == 429 and len(config.GROQ_API_KEYS) > 1:
                    _rotate_groq_key()
                    # Don't count this as a normal retry — try again immediately
                    continue

                if attempt == config.MAX_RETRIES:
                    if model_tier == "primary":
                        logger.warning(
                            f"  ⚠ Primary model {current_model} exhausted. "
                            f"Falling back to {fallback_model}..."
                        )
                        break  # Break to try fallback
                    else:
                        raise LLMError(role, provider, current_model, status_code, error_str, error_type)

                wait_time = config.RETRY_BACKOFF_BASE ** attempt
                logger.info(f"  ⏳ Retrying in {wait_time}s...")
                time.sleep(wait_time)

    # Should not reach here, but just in case
    raise LLMError(role, provider, model, 0, "All retries and fallbacks exhausted", "unknown")


def extract_json(text: str) -> dict | list:
    """Extract and parse JSON from a model response (handles markdown code blocks)."""
    # Try to find JSON in code blocks first
    json_match = re.search(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group(1))
    # Try raw parse
    # Find the first [ or { and parse from there
    for i, c in enumerate(text):
        if c in "[{":
            try:
                return json.loads(text[i:])
            except json.JSONDecodeError:
                continue
    return json.loads(text)


def extract_python_code(text: str) -> str:
    """Extract Python code from a model response (handles markdown code blocks)."""
    code_match = re.search(r'```(?:python)?\s*\n(.*?)\n```', text, re.DOTALL)
    if code_match:
        return code_match.group(1)
    return text
