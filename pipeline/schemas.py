"""
AEVE 2.0 — Pydantic v2 schemas that gate every phase boundary.

Every LLM/agent handoff must produce data that validates against one of the
models below. On validation failure: 1 retry with the validation error
injected into the prompt → provider fallback → schema-level fallback stub.

Models are pure data contracts. No I/O, no LLM calls, no side effects here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Phase 0 — StyleManifest (deterministic, no LLM)
# ---------------------------------------------------------------------------

PaletteKey = Literal["bg", "primary", "accent", "muted", "success", "warn"]
FontFamily = Literal["Inter", "Latin Modern Roman", "JetBrains Mono"]
TransitionKind = Literal["FadeOut", "ReplacementTransform"]
LayoutZoneName = Literal[
    "title", "main", "caption", "left_rail", "right_rail", "footer"
]


class StyleManifest(BaseModel):
    """Deterministic visual contract injected into every Director/Animator prompt."""

    model_config = ConfigDict(extra="forbid")

    palette: dict[PaletteKey, str] = Field(
        ..., description="Hex color codes keyed by semantic role"
    )
    font: FontFamily = "Inter"
    base_font_size: int = Field(default=36, ge=20, le=72)
    frame_margin: float = Field(default=0.5, ge=0.0, le=2.0)
    transition: TransitionKind = "FadeOut"
    layout_zones: dict[LayoutZoneName, tuple[float, float]] = Field(
        ...,
        description="(x, y) Manim coordinates per named zone, e.g. {'title': (0.0, 3.2)}",
    )

    @field_validator("palette")
    @classmethod
    def _palette_has_required_keys(cls, v: dict[str, str]) -> dict[str, str]:
        required = {"bg", "primary", "accent", "muted", "success", "warn"}
        missing = required - set(v)
        if missing:
            raise ValueError(f"palette missing keys: {sorted(missing)}")
        for key, value in v.items():
            if not (isinstance(value, str) and value.startswith("#") and len(value) in (4, 7, 9)):
                raise ValueError(f"palette[{key}] must be hex like '#RRGGBB' (got {value!r})")
        return v


# ---------------------------------------------------------------------------
# Phase 1 — Solver → DeepSolution
# ---------------------------------------------------------------------------

Difficulty = Literal["intro", "intermediate", "advanced"]


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")

    narrative: str = Field(..., min_length=1)
    latex: str | None = None
    visual_intent: str = Field(..., min_length=1)


class DeepSolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str = Field(..., min_length=1)
    difficulty: Difficulty
    prerequisites: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(..., min_length=1)
    conclusion: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Phase 2 — Director → Storyboard
# ---------------------------------------------------------------------------

LayoutTemplate = Literal[
    "title_only",
    "title_plus_eq",
    "equation_focus",
    "graph",
    "derivation_chain",
    "split_eq_text",
]
TransitionIn = Literal["fade", "slide_left", "none"]


class StoryboardScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str = Field(..., pattern=r"^\d{3}$", description="Zero-padded, e.g. '001'")
    title: str = Field(..., min_length=1)
    key_concept: str = Field(..., min_length=1)
    narration_draft: str = Field(
        ...,
        min_length=1,
        description="≤2 sentences; math expressed in raw LaTeX",
    )
    formulas: list[str] = Field(default_factory=list, description="Raw LaTeX, no $$ delimiters")
    visual_intent: str = Field(..., min_length=1)
    layout: LayoutTemplate
    carryover_objects: list[str] = Field(
        default_factory=list,
        description="Names of objects from this scene to carry into the next",
    )
    transition_in: TransitionIn = "fade"


class Storyboard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_target_seconds: int = Field(..., ge=20, le=180)
    scenes: list[StoryboardScene] = Field(..., min_length=1, max_length=10)

    @field_validator("scenes")
    @classmethod
    def _scene_ids_unique_and_ordered(cls, v: list[StoryboardScene]) -> list[StoryboardScene]:
        ids = [s.scene_id for s in v]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate scene_id in storyboard: {ids}")
        if ids != sorted(ids):
            raise ValueError(f"scene_ids must be ascending: got {ids}")
        return v


# ---------------------------------------------------------------------------
# Phase 3 — Narrator + TTS → SceneAudio
# ---------------------------------------------------------------------------


class WordEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    word: str
    start_s: float = Field(..., ge=0.0)
    end_s: float = Field(..., ge=0.0)

    @field_validator("end_s")
    @classmethod
    def _end_after_start(cls, v: float, info) -> float:
        start = info.data.get("start_s", 0.0)
        if v < start:
            raise ValueError(f"end_s ({v}) < start_s ({start})")
        return v


class SceneAudio(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    scene_id: str = Field(..., pattern=r"^\d{3}$")
    mp3_path: Path
    duration_s: float = Field(..., gt=0.0, description="ffprobe-measured, authoritative")
    word_timeline: list[WordEvent] = Field(default_factory=list)
    narration_final: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Phase 4 — Animator → SceneCode
# ---------------------------------------------------------------------------


class SceneCode(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    scene_id: str = Field(..., pattern=r"^\d{3}$")
    py_path: Path
    class_name: str = Field(..., min_length=1)
    target_runtime_s: float = Field(..., gt=0.0)
    ast_validated: bool
    predicted_runtime_s: float = Field(..., gt=0.0)


# ---------------------------------------------------------------------------
# Phase 5 — Render + Healer → SceneVideo
# ---------------------------------------------------------------------------


class SceneVideo(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    scene_id: str = Field(..., pattern=r"^\d{3}$")
    mp4_path: Path
    measured_duration_s: float = Field(..., gt=0.0, description="ffprobe-measured")
    drift_ms: int = Field(..., description="measured - target, in milliseconds")
    used_healer: bool = False
    healer_attempts: int = Field(default=0, ge=0, le=4)


# ---------------------------------------------------------------------------
# Phase 6 — Assembler → FinalVideo
# ---------------------------------------------------------------------------


class FinalVideo(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    mp4_path: Path
    total_duration_s: float = Field(..., gt=0.0)
    scene_count: int = Field(..., ge=1)
    total_drift_ms: int = Field(
        ..., description="|final - sum(scene_audio_durations)| in ms; CI gate is <50"
    )


# ---------------------------------------------------------------------------
# Carryover (cross-scene continuity primitive)
# ---------------------------------------------------------------------------


class CarryObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Variable name in the prior scene")
    kind: str = Field(..., description="e.g. 'MathTex', 'Text', 'Polygon'")
    position: tuple[float, float, float] = Field(
        ..., description="(x, y, z) in Manim units"
    )


class SceneCarry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str = Field(..., pattern=r"^\d{3}$")
    objects: list[CarryObject] = Field(default_factory=list)


__all__ = [
    "CarryObject",
    "DeepSolution",
    "Difficulty",
    "FinalVideo",
    "FontFamily",
    "LayoutTemplate",
    "LayoutZoneName",
    "PaletteKey",
    "SceneAudio",
    "SceneCarry",
    "SceneCode",
    "SceneVideo",
    "Step",
    "Storyboard",
    "StoryboardScene",
    "StyleManifest",
    "TransitionIn",
    "TransitionKind",
    "WordEvent",
]
