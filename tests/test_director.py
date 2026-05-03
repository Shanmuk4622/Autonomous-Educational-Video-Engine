"""
Unit tests for pipeline.director — parser robustness + prompt invariants.
"""

from __future__ import annotations

import json

import pytest

from pipeline.director import LAYOUT_NAMES, TRANSITION_NAMES, _parse, _system_prompt
from pipeline.schemas import Storyboard


_VALID_STORYBOARD = {
    "total_target_seconds": 60,
    "scenes": [
        {
            "scene_id": "001",
            "title": "Intro",
            "key_concept": "Set up the right triangle and the claim.",
            "narration_draft": "We start with a right triangle whose legs are a and b.",
            "formulas": ["a^2 + b^2 = c^2"],
            "visual_intent": "show a right triangle",
            "layout": "title_only",
            "carryover_objects": [],
            "transition_in": "fade",
        },
        {
            "scene_id": "002",
            "title": "Squares on the sides",
            "key_concept": "Attach squares to each leg and the hypotenuse.",
            "narration_draft": "Now build a square on each side. Their areas are a squared, b squared, c squared.",
            "formulas": [],
            "visual_intent": "three colored squares attached to triangle",
            "layout": "equation_focus",
            "carryover_objects": ["triangle"],
            "transition_in": "fade",
        },
    ],
}


def test_parses_clean_json():
    sb = _parse(json.dumps(_VALID_STORYBOARD))
    assert isinstance(sb, Storyboard)
    assert sb.total_target_seconds == 60
    assert len(sb.scenes) == 2
    assert sb.scenes[0].scene_id == "001"


def test_parses_fenced_json():
    fenced = "```json\n" + json.dumps(_VALID_STORYBOARD) + "\n```"
    sb = _parse(fenced)
    assert sb.scenes[1].layout == "equation_focus"


def test_unwraps_single_key_wrapper():
    wrapped = {"storyboard": _VALID_STORYBOARD}
    sb = _parse(json.dumps(wrapped))
    assert len(sb.scenes) == 2


def test_rejects_unsorted_scene_ids():
    bad = dict(_VALID_STORYBOARD)
    bad["scenes"] = [_VALID_STORYBOARD["scenes"][1], _VALID_STORYBOARD["scenes"][0]]
    with pytest.raises(Exception):
        _parse(json.dumps(bad))


def test_rejects_unknown_layout():
    bad = json.loads(json.dumps(_VALID_STORYBOARD))
    bad["scenes"][0]["layout"] = "fancy_layout"
    with pytest.raises(Exception):
        _parse(json.dumps(bad))


def test_system_prompt_lists_all_layouts_and_transitions():
    prompt = _system_prompt(60)
    for name in LAYOUT_NAMES:
        assert name in prompt, f"layout {name!r} missing from director system prompt"
    for name in TRANSITION_NAMES:
        assert name in prompt, f"transition {name!r} missing from director system prompt"


def test_system_prompt_mentions_target_seconds():
    assert "45" in _system_prompt(45)
    assert "120" in _system_prompt(120)
