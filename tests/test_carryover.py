"""
Tests for pipeline.carryover — read/write SceneCarry JSON.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.carryover import (
    carry_path,
    empty_carry,
    predict_carry_from_storyboard,
    read_carry,
    write_carry,
)
from pipeline.schemas import CarryObject, SceneCarry, StoryboardScene
from pipeline.style import build_style_manifest


def test_carry_path_canonical(tmp_path: Path):
    p = carry_path(tmp_path, "002")
    assert p.name == "scene_002.carry.json"
    assert p.parent == tmp_path


def test_read_missing_returns_empty(tmp_path: Path):
    carry = read_carry(tmp_path, "001")
    assert isinstance(carry, SceneCarry)
    assert carry.scene_id == "001"
    assert carry.objects == []


def test_write_then_read_roundtrip(tmp_path: Path):
    original = SceneCarry(
        scene_id="003",
        objects=[
            CarryObject(name="title", kind="Text", position=(0.0, 3.2, 0.0)),
            CarryObject(name="formula", kind="MathTex", position=(0.0, 0.4, 0.0)),
        ],
    )
    path = write_carry(tmp_path, original)
    assert path.exists()
    restored = read_carry(tmp_path, "003")
    assert restored == original


def test_malformed_file_treated_as_empty(tmp_path: Path):
    path = carry_path(tmp_path, "004")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    carry = read_carry(tmp_path, "004")
    assert carry.objects == []


def test_empty_carry_helper():
    c = empty_carry("007")
    assert c.scene_id == "007"
    assert c.objects == []


# ---------------------------------------------------------------------------
# predict_carry_from_storyboard
# ---------------------------------------------------------------------------


def _scene(scene_id: str, *, carry: list[str]) -> StoryboardScene:
    return StoryboardScene(
        scene_id=scene_id,
        title="t",
        key_concept="k",
        narration_draft="d",
        formulas=[],
        visual_intent="v",
        layout="title_only",
        carryover_objects=carry,
        transition_in="fade",
    )


def test_predict_carry_empty_when_no_carryover_objects():
    style = build_style_manifest()
    prior = _scene("001", carry=[])
    carry = predict_carry_from_storyboard(prior, style)
    assert carry.scene_id == "001"
    assert carry.objects == []


def test_predict_carry_maps_title_to_title_zone():
    style = build_style_manifest()
    prior = _scene("001", carry=["title"])
    carry = predict_carry_from_storyboard(prior, style)
    assert len(carry.objects) == 1
    obj = carry.objects[0]
    assert obj.name == "title"
    assert obj.kind == "Text"
    # title zone is (0.0, 3.2) by default
    assert obj.position == (0.0, 3.2, 0.0)


def test_predict_carry_maps_formula_to_main_zone_with_mathtex_kind():
    style = build_style_manifest()
    prior = _scene("002", carry=["formula_main", "eq_a"])
    carry = predict_carry_from_storyboard(prior, style)
    by_name = {o.name: o for o in carry.objects}
    assert by_name["formula_main"].kind == "MathTex"
    assert by_name["eq_a"].kind == "MathTex"
    # main zone is (0.0, 0.4) by default
    assert by_name["formula_main"].position[1] == 0.4


def test_predict_carry_caption_and_label_use_caption_zone():
    style = build_style_manifest()
    prior = _scene("003", carry=["caption_x", "label_y"])
    carry = predict_carry_from_storyboard(prior, style)
    by_name = {o.name: o for o in carry.objects}
    # both should use MarkupText kind
    assert by_name["caption_x"].kind == "MarkupText"
    assert by_name["label_y"].kind == "MarkupText"
    # caption zone is (0.0, -3.0)
    assert by_name["caption_x"].position == (0.0, -3.0, 0.0)


def test_predict_carry_left_right_rails():
    style = build_style_manifest()
    prior = _scene("004", carry=["left_panel", "right_panel"])
    carry = predict_carry_from_storyboard(prior, style)
    by_name = {o.name: o for o in carry.objects}
    assert by_name["left_panel"].position[0] == -4.8
    assert by_name["right_panel"].position[0] == 4.8


def test_predict_carry_strips_blank_names():
    style = build_style_manifest()
    prior = _scene("005", carry=["title", "", "  ", "formula"])
    carry = predict_carry_from_storyboard(prior, style)
    assert {o.name for o in carry.objects} == {"title", "formula"}
