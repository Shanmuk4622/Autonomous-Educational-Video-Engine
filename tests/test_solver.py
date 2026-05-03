"""
Unit tests for pipeline.solver.

Live calls (real Groq/OpenRouter keys) are gated behind `pytest.mark.live`.
The default suite tests the parser robustness in isolation — no network.
"""

from __future__ import annotations

import json

import pytest

from pipeline.schemas import DeepSolution
from pipeline.solver import _parse


# ---------------------------------------------------------------------------
# _parse — must accept clean JSON, fenced JSON, and unwrap single-key wrappers
# ---------------------------------------------------------------------------


_VALID_PAYLOAD = {
    "topic": "Pythagorean theorem",
    "difficulty": "intermediate",
    "prerequisites": ["right triangles"],
    "steps": [
        {
            "narrative": "Draw a right triangle with legs a and b, hypotenuse c.",
            "latex": "a^2 + b^2 = c^2",
            "visual_intent": "show three squares attached to a triangle",
        }
    ],
    "conclusion": "The squares on the legs sum to the square on the hypotenuse.",
}


def test_parses_clean_json():
    sol = _parse(json.dumps(_VALID_PAYLOAD))
    assert isinstance(sol, DeepSolution)
    assert sol.topic == "Pythagorean theorem"
    assert sol.difficulty == "intermediate"
    assert len(sol.steps) == 1


def test_parses_fenced_json():
    fenced = "```json\n" + json.dumps(_VALID_PAYLOAD) + "\n```"
    sol = _parse(fenced)
    assert sol.steps[0].latex == "a^2 + b^2 = c^2"


def test_unwraps_single_key_wrapper():
    wrapped = {"response": _VALID_PAYLOAD}
    sol = _parse(json.dumps(wrapped))
    assert sol.topic == "Pythagorean theorem"


def test_rejects_empty_output():
    with pytest.raises(ValueError, match="empty"):
        _parse("")


def test_rejects_non_json():
    with pytest.raises(ValueError, match="not valid JSON"):
        _parse("hello, here is your solution")


def test_rejects_invalid_schema():
    bad = dict(_VALID_PAYLOAD)
    bad["difficulty"] = "expert"  # not in Literal
    with pytest.raises(Exception):
        _parse(json.dumps(bad))


def test_rejects_empty_steps():
    bad = dict(_VALID_PAYLOAD)
    bad["steps"] = []
    with pytest.raises(Exception):
        _parse(json.dumps(bad))


# ---------------------------------------------------------------------------
# Live test — only runs with `pytest -m live`. Hits real Groq keys.
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_solver_live_pythagorean():
    from pipeline.solver import solve

    sol = await solve("Prove the Pythagorean theorem with a square-rearrangement proof.")
    assert isinstance(sol, DeepSolution)
    assert sol.steps, "solver must return at least one step"
    assert "pythag" in sol.topic.lower() or "right" in sol.topic.lower()
