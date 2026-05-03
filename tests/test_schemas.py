"""
Round-trip + validation tests for pipeline.schemas.

CI gate #3: every Pydantic model must serialize/deserialize losslessly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline.schemas import (
    CarryObject,
    DeepSolution,
    FinalVideo,
    SceneAudio,
    SceneCarry,
    SceneCode,
    SceneVideo,
    Step,
    Storyboard,
    StoryboardScene,
    StyleManifest,
    WordEvent,
)
from pipeline.style import build_style_manifest


# ---------------------------------------------------------------------------
# StyleManifest
# ---------------------------------------------------------------------------


def test_style_manifest_default_roundtrip():
    m = build_style_manifest()
    dumped = m.model_dump()
    restored = StyleManifest.model_validate(dumped)
    assert restored == m


def test_style_manifest_palette_must_have_six_keys():
    with pytest.raises(ValidationError):
        StyleManifest(
            palette={"bg": "#000000"},  # type: ignore[arg-type]
            font="Inter",
            transition="FadeOut",
            layout_zones={"title": (0.0, 3.2)},
        )


def test_style_manifest_palette_hex_only():
    bad_palette = {
        "bg": "black",
        "primary": "#fff",
        "accent": "#FFD166",
        "muted": "#7A8AA8",
        "success": "#4ADE80",
        "warn": "#F87171",
    }
    with pytest.raises(ValidationError):
        StyleManifest(
            palette=bad_palette,  # type: ignore[arg-type]
            font="Inter",
            transition="FadeOut",
            layout_zones={"title": (0.0, 3.2)},
        )


# ---------------------------------------------------------------------------
# DeepSolution
# ---------------------------------------------------------------------------


def test_deep_solution_roundtrip():
    sol = DeepSolution(
        topic="Pythagorean theorem",
        difficulty="intermediate",
        prerequisites=["right triangles", "area"],
        steps=[
            Step(
                narrative="Construct a square on each side of a right triangle.",
                latex="a^2 + b^2 = c^2",
                visual_intent="three squares attached to a triangle",
            ),
        ],
        conclusion="The two smaller squares' areas sum to the largest square's area.",
    )
    restored = DeepSolution.model_validate_json(sol.model_dump_json())
    assert restored == sol


def test_deep_solution_requires_at_least_one_step():
    with pytest.raises(ValidationError):
        DeepSolution(
            topic="t",
            difficulty="intro",
            prerequisites=[],
            steps=[],
            conclusion="c",
        )


# ---------------------------------------------------------------------------
# Storyboard
# ---------------------------------------------------------------------------


def _scene(idx: int, **overrides) -> StoryboardScene:
    base = dict(
        scene_id=f"{idx:03d}",
        title=f"Scene {idx}",
        key_concept="concept",
        narration_draft="A short narration. Two sentences max.",
        formulas=[r"a^2 + b^2 = c^2"],
        visual_intent="show squares",
        layout="title_plus_eq",
        carryover_objects=[],
        transition_in="fade",
    )
    base.update(overrides)
    return StoryboardScene(**base)  # type: ignore[arg-type]


def test_storyboard_roundtrip():
    sb = Storyboard(
        total_target_seconds=60,
        scenes=[_scene(1), _scene(2), _scene(3)],
    )
    restored = Storyboard.model_validate_json(sb.model_dump_json())
    assert restored == sb


def test_storyboard_rejects_duplicate_scene_ids():
    with pytest.raises(ValidationError):
        Storyboard(
            total_target_seconds=60,
            scenes=[_scene(1), _scene(1)],
        )


def test_storyboard_rejects_unsorted_scene_ids():
    with pytest.raises(ValidationError):
        Storyboard(
            total_target_seconds=60,
            scenes=[_scene(2), _scene(1)],
        )


def test_storyboard_total_seconds_bounds():
    with pytest.raises(ValidationError):
        Storyboard(total_target_seconds=10, scenes=[_scene(1)])
    with pytest.raises(ValidationError):
        Storyboard(total_target_seconds=999, scenes=[_scene(1)])


def test_scene_id_format_enforced():
    with pytest.raises(ValidationError):
        _scene(1, scene_id="1")  # not zero-padded
    with pytest.raises(ValidationError):
        _scene(1, scene_id="0001")  # too long


# ---------------------------------------------------------------------------
# WordEvent / SceneAudio
# ---------------------------------------------------------------------------


def test_word_event_end_must_follow_start():
    with pytest.raises(ValidationError):
        WordEvent(word="hi", start_s=2.0, end_s=1.0)


def test_scene_audio_roundtrip(tmp_path: Path):
    mp3 = tmp_path / "scene_001.mp3"
    mp3.write_bytes(b"fake")
    audio = SceneAudio(
        scene_id="001",
        mp3_path=mp3,
        duration_s=12.345,
        word_timeline=[
            WordEvent(word="hello", start_s=0.0, end_s=0.5),
            WordEvent(word="world", start_s=0.5, end_s=1.0),
        ],
        narration_final="hello world",
    )
    restored = SceneAudio.model_validate_json(audio.model_dump_json())
    assert restored.scene_id == audio.scene_id
    assert restored.duration_s == pytest.approx(audio.duration_s)
    assert len(restored.word_timeline) == 2


def test_scene_audio_duration_must_be_positive():
    with pytest.raises(ValidationError):
        SceneAudio(
            scene_id="001",
            mp3_path=Path("nope.mp3"),
            duration_s=0.0,
            narration_final="x",
        )


# ---------------------------------------------------------------------------
# SceneCode / SceneVideo / FinalVideo
# ---------------------------------------------------------------------------


def test_scene_code_roundtrip(tmp_path: Path):
    py = tmp_path / "scene_001.py"
    py.write_text("# stub", encoding="utf-8")
    code = SceneCode(
        scene_id="001",
        py_path=py,
        class_name="Scene001",
        target_runtime_s=12.0,
        ast_validated=True,
        predicted_runtime_s=11.7,
    )
    restored = SceneCode.model_validate_json(code.model_dump_json())
    assert restored.class_name == "Scene001"


def test_scene_video_drift_can_be_signed(tmp_path: Path):
    mp4 = tmp_path / "scene_001.mp4"
    mp4.write_bytes(b"fake")
    v_pos = SceneVideo(
        scene_id="001", mp4_path=mp4, measured_duration_s=12.0, drift_ms=37
    )
    v_neg = SceneVideo(
        scene_id="001", mp4_path=mp4, measured_duration_s=12.0, drift_ms=-22
    )
    assert v_pos.drift_ms == 37
    assert v_neg.drift_ms == -22


def test_final_video_roundtrip(tmp_path: Path):
    mp4 = tmp_path / "final.mp4"
    mp4.write_bytes(b"fake")
    fv = FinalVideo(
        mp4_path=mp4,
        total_duration_s=62.5,
        scene_count=6,
        total_drift_ms=18,
    )
    restored = FinalVideo.model_validate_json(fv.model_dump_json())
    assert restored == fv


# ---------------------------------------------------------------------------
# SceneCarry
# ---------------------------------------------------------------------------


def test_scene_carry_roundtrip():
    carry = SceneCarry(
        scene_id="002",
        objects=[
            CarryObject(name="title", kind="Text", position=(0.0, 3.2, 0.0)),
            CarryObject(name="formula", kind="MathTex", position=(0.0, 0.4, 0.0)),
        ],
    )
    restored = SceneCarry.model_validate_json(carry.model_dump_json())
    assert restored == carry
