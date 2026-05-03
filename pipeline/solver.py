"""
AEVE 2.0 — Phase 1: Solver.

Takes a free-text user query and returns a `DeepSolution` — a structured,
schema-validated mathematical solution with prerequisites, ordered steps, and a
conclusion. The Solver is the first LLM hop and the foundation for every later
phase: the Director plans scenes from these steps, the Narrator polishes their
prose, and the Animator visualizes their LaTeX.

Routing (see registry.ROUTES["solver"]):
    primary    — Groq moonshotai/kimi-k2-instruct (math benchmark leader)
    fallback 1 — OpenRouter deepseek/deepseek-chat-v3
    fallback 2 — Groq llama-3.3-70b-versatile

Robustness:
    1. JSON-only response_format on every call.
    2. `call_agent_json` runs the parser; on validation failure it injects the
       error back into the prompt for one repair round per spec.
    3. If all specs+repairs fail, raises `LLMError` (caller decides fallback).
"""

from __future__ import annotations

import json
import logging
import re

from pipeline.llm_clients import call_agent_json
from pipeline.schemas import DeepSolution

logger = logging.getLogger("AEVE")

SYSTEM_PROMPT = """You are a careful, rigorous mathematician.

Given a user's question, return ONE JSON object — and nothing else — that
matches this exact schema:

{
  "topic":         <string, the subject in 1-6 words>,
  "difficulty":    <"intro" | "intermediate" | "advanced">,
  "prerequisites": <array of short strings, may be empty>,
  "steps": [
    {
      "narrative":     <one or two prose sentences explaining this step>,
      "latex":         <raw LaTeX without $...$ delimiters, or null if none>,
      "visual_intent": <short imperative describing what to draw/animate>
    },
    ...
  ],
  "conclusion": <one or two sentences summarizing the result>
}

Rules:
- Output ONLY the JSON object. No prose, no markdown fences, no commentary.
- Use raw LaTeX in `latex` fields (e.g. "a^2 + b^2 = c^2"), no surrounding $.
- Keep `narrative` and `visual_intent` concise — these become voiceover and
  animation hints downstream.
- 4-8 steps is typical. Never fewer than 1.
- `difficulty` reflects the audience the explanation targets, not the proof's
  complexity.
"""


def _parse(raw: str) -> DeepSolution:
    """Parser injected into call_agent_json. Strips fences, validates schema."""
    text = _strip_code_fences(raw).strip()
    if not text:
        raise ValueError("solver returned empty content")
    try:
        # Validate via JSON path (gives the cleanest error message).
        return DeepSolution.model_validate_json(text)
    except Exception:
        # Some providers wrap the object in `{"response": {...}}`. Best-effort
        # rescue: if the top level has exactly one dict-typed value, unwrap.
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"solver output is not valid JSON: {e}") from e
        if isinstance(obj, dict) and len(obj) == 1:
            inner = next(iter(obj.values()))
            if isinstance(inner, dict):
                return DeepSolution.model_validate(inner)
        raise


_FENCE_RE = re.compile(r"^```(?:json|JSON)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _strip_code_fences(text: str) -> str:
    m = _FENCE_RE.match(text.strip())
    return m.group(1) if m else text


async def solve(query: str, *, image_hint: str | None = None) -> DeepSolution:
    """Run the Solver agent on a user query.

    Args:
        query: Free-text question, e.g. "Prove the Pythagorean theorem."
        image_hint: Optional caption/description if the user uploaded an image.

    Returns:
        A schema-validated `DeepSolution`.

    Raises:
        LLMError: every spec in the solver chain failed (with repair rounds).
    """
    user_prompt = query.strip()
    if image_hint:
        user_prompt = f"{user_prompt}\n\n[Visual context: {image_hint.strip()}]"

    logger.info("[solver] querying with %d-char prompt", len(user_prompt))
    solution: DeepSolution = await call_agent_json(
        role="solver",
        user_prompt=user_prompt,
        system_prompt=SYSTEM_PROMPT,
        parser=_parse,
    )
    logger.info(
        "[solver] ok — topic=%r difficulty=%s steps=%d",
        solution.topic,
        solution.difficulty,
        len(solution.steps),
    )
    return solution


__all__ = ["solve", "SYSTEM_PROMPT"]
