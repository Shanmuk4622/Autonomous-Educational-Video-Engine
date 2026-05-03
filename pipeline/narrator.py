"""
AEVE 2.0 — Phase 3a: Narrator.

Takes a `StoryboardScene` and produces clean, spoken-English narration ready
for edge-tts. The Director's `narration_draft` may contain raw LaTeX (e.g.
`a^2 + b^2 = c^2`); the Narrator's job is to expand that into words a TTS
engine can speak naturally ("a squared plus b squared equals c squared").

Routing (see registry.ROUTES["narrator"]):
    primary    — Groq llama-3.3-70b-versatile
    fallback 1 — OpenRouter zai/glm-4.6
    fallback 2 — Groq llama-3.1-8b-instant

The Narrator returns plain text (not JSON). We use `call_agent` not
`call_agent_json`, but still gate the output: it must be non-empty, ≤400
characters (so a single scene stays well under 25s spoken at ~150 wpm), and
free of bracketed/dollar-delimited LaTeX residue.
"""

from __future__ import annotations

import logging
import re

from pipeline.llm_clients import call_agent
from pipeline.schemas import StoryboardScene

logger = logging.getLogger("AEVE")

MAX_NARRATION_CHARS = 400

SYSTEM_PROMPT = """You rewrite short scene drafts into clean spoken English
for a text-to-speech engine.

Rules:
- Output ONE block of plain prose. No JSON, no markdown, no SSML, no quotes.
- Expand all LaTeX into spoken words:
    a^2          → "a squared"
    \\frac{a}{b} → "a over b"
    \\sqrt{x}    → "the square root of x"
    \\pi         → "pi"
    =            → "equals"
    +, -, *      → "plus", "minus", "times"
- Keep it to AT MOST 2 sentences and 60 words. Shorter is better.
- Natural cadence: contractions allowed, no formal proof phrasing.
- Never start with "In this scene" or similar meta-narration.
- Never include the LaTeX source itself or any backslashes.
"""


_LATEX_RESIDUE_RE = re.compile(r"\\[a-zA-Z]+|\$+|\^|_\{|\\frac|\\sqrt")


def _validate(text: str) -> str:
    text = text.strip()
    if not text:
        raise ValueError("narrator returned empty text")
    # Strip surrounding quotes if the model added them.
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    if len(text) > MAX_NARRATION_CHARS:
        # Hard truncate at sentence boundary if possible.
        cut = text.rfind(". ", 0, MAX_NARRATION_CHARS)
        text = text[: cut + 1] if cut > 0 else text[:MAX_NARRATION_CHARS]
    if _LATEX_RESIDUE_RE.search(text):
        raise ValueError(f"narration still contains LaTeX residue: {text!r}")
    return text


def _user_prompt(scene: StoryboardScene) -> str:
    formulas_block = ""
    if scene.formulas:
        formulas_block = "\n\nFormulas referenced (for context only — speak them in words):\n" + "\n".join(
            f"  - {f}" for f in scene.formulas
        )
    return (
        f"Scene title: {scene.title}\n"
        f"Key concept: {scene.key_concept}\n"
        f"Draft (may contain LaTeX): {scene.narration_draft}"
        f"{formulas_block}\n\n"
        "Rewrite the draft as spoken English, ≤2 sentences."
    )


async def polish(scene: StoryboardScene) -> str:
    """Convert a StoryboardScene's narration_draft into TTS-ready prose."""
    logger.info("[narrator] scene %s — polishing draft", scene.scene_id)
    raw = await call_agent(
        role="narrator",
        user_prompt=_user_prompt(scene),
        system_prompt=SYSTEM_PROMPT,
    )
    final = _validate(raw)
    logger.info("[narrator] scene %s — %d chars", scene.scene_id, len(final))
    return final


__all__ = ["polish", "MAX_NARRATION_CHARS"]
