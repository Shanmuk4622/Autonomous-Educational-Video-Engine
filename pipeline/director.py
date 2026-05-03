"""
AEVE 2.0 — Phase 2: Director.

Takes a `DeepSolution` and produces a `Storyboard` — a per-scene plan that the
Narrator and Animator consume independently. The Director is the only LLM in
the pipeline that picks layouts; the Animator never invents coordinates.

Routing (see registry.ROUTES["director"]):
    primary    — Groq llama-3.3-70b-versatile
    fallback 1 — OpenRouter meta-llama/llama-3.3-70b-instruct
    fallback 2 — Groq llama-3.1-8b-instant

Contract enforced by the Storyboard schema:
    - 1-10 scenes
    - scene_ids zero-padded (`001`, `002`, …) and strictly ascending
    - `narration_draft` is ≤2 sentences, math in raw LaTeX
    - `layout` is one of the six fixed templates
    - `total_target_seconds` ∈ [20, 180]
"""

from __future__ import annotations

import json
import logging
import re
from typing import get_args

from pipeline.llm_clients import call_agent_json
from pipeline.schemas import (
    DeepSolution,
    LayoutTemplate,
    Storyboard,
    StyleManifest,
    TransitionIn,
)
from pipeline.style import manifest_to_prompt_block

logger = logging.getLogger("AEVE")

LAYOUT_NAMES: tuple[str, ...] = get_args(LayoutTemplate)
TRANSITION_NAMES: tuple[str, ...] = get_args(TransitionIn)


def _system_prompt(target_seconds: int) -> str:
    return f"""You are a senior director planning a short, narrated math video.

Given a verified mathematical solution, output ONE JSON object matching this
schema EXACTLY (no extra keys):

{{
  "total_target_seconds": <int in [20, 180]>,
  "scenes": [
    {{
      "scene_id":          <"001", "002", ... zero-padded ascending>,
      "title":             <2-6 word screen title>,
      "key_concept":       <one short sentence — what this scene teaches>,
      "narration_draft":   <≤2 sentences. Math expressed in raw LaTeX, no $>,
      "formulas":          <array of raw LaTeX strings (no $$). May be empty>,
      "visual_intent":     <one sentence — what the animation should show>,
      "layout":            <one of: {", ".join(LAYOUT_NAMES)}>,
      "carryover_objects": <names of objects to reuse in next scene; may be []>,
      "transition_in":     <one of: {", ".join(TRANSITION_NAMES)}>
    }},
    ...
  ]
}}

Rules:
- Output ONLY the JSON object. No prose, no markdown fences.
- Aim for `total_target_seconds` ≈ {target_seconds}. Honor [20, 180] bounds.
- Plan 4-7 scenes for a {target_seconds}s video; never more than 10.
- `narration_draft` MUST be at most two sentences. Long explanations belong to
  the visual, not the voiceover.
- Scenes flow: setup → derivation → conclusion. Reuse carryover_objects to
  morph one scene into the next instead of cutting hard.
- Pick the simplest layout that fits:
    title_only        — opening/closing, single phrase on screen
    title_plus_eq     — short title with one equation
    equation_focus    — single equation, possibly transformed step-by-step
    graph             — Axes/NumberPlane/FunctionGraph dominates the frame
    derivation_chain  — vertical chain of equations being rewritten
    split_eq_text     — equation on one side, prose on the other
- Never duplicate scene_ids. They MUST be ascending starting at "001".
"""


def _strip_code_fences(text: str) -> str:
    m = re.match(r"^```(?:json|JSON)?\s*\n(.*?)\n```\s*$", text.strip(), re.DOTALL)
    return m.group(1) if m else text


def _parse(raw: str) -> Storyboard:
    text = _strip_code_fences(raw).strip()
    if not text:
        raise ValueError("director returned empty content")
    try:
        return Storyboard.model_validate_json(text)
    except Exception:
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"director output is not valid JSON: {e}") from e
        if isinstance(obj, dict) and len(obj) == 1:
            inner = next(iter(obj.values()))
            if isinstance(inner, dict):
                return Storyboard.model_validate(inner)
        raise


def _solution_to_prompt(solution: DeepSolution) -> str:
    """Compact, deterministic rendering of the solution for the prompt body."""
    payload = {
        "topic": solution.topic,
        "difficulty": solution.difficulty,
        "prerequisites": solution.prerequisites,
        "steps": [
            {
                "narrative": s.narrative,
                "latex": s.latex,
                "visual_intent": s.visual_intent,
            }
            for s in solution.steps
        ],
        "conclusion": solution.conclusion,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


async def direct(
    solution: DeepSolution,
    *,
    target_seconds: int = 60,
    style: StyleManifest | None = None,
) -> Storyboard:
    """Run the Director agent on a verified solution.

    Args:
        solution: The validated DeepSolution from the Solver.
        target_seconds: Desired total runtime (clamped to [20, 180] by schema).
        style: Optional StyleManifest. If supplied, its JSON form is included
            in the prompt so the Director keeps its plan consistent with the
            visual contract (palette / layout zones).

    Returns:
        A schema-validated `Storyboard`.

    Raises:
        LLMError: every spec in the director chain failed.
    """
    target_seconds = max(20, min(180, int(target_seconds)))

    user_prompt_parts: list[str] = [
        "Plan a video for this verified solution:",
        "",
        _solution_to_prompt(solution),
    ]
    if style is not None:
        user_prompt_parts += [
            "",
            "Visual style contract (every scene must respect these zones/colors):",
            manifest_to_prompt_block(style),
        ]
    user_prompt = "\n".join(user_prompt_parts)

    logger.info(
        "[director] planning storyboard for topic=%r target=%ds steps=%d",
        solution.topic,
        target_seconds,
        len(solution.steps),
    )
    storyboard: Storyboard = await call_agent_json(
        role="director",
        user_prompt=user_prompt,
        system_prompt=_system_prompt(target_seconds),
        parser=_parse,
    )
    logger.info(
        "[director] ok — %d scenes totaling %ds",
        len(storyboard.scenes),
        storyboard.total_target_seconds,
    )
    return storyboard


__all__ = ["direct"]
