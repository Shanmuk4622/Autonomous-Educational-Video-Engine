"""
AEVE 2.0 — cross-scene continuity (carryover) helpers.

Each scene may persist a small JSON manifest of objects that survive into the
next scene — typically a title `Text` or a formula `MathTex` we want to morph
rather than recreate. The Animator for scene N+1 receives scene N's
`SceneCarry` and must `self.add(...)` (or `ReplacementTransform`) those named
objects before introducing new ones.

This module is plumbing: read/write/diff. The actual carry-out content is
emitted by the Animator-generated Manim code at render time.

Convention:
    output/scenes/scene_<id>.carry.json    →  SceneCarry JSON

`read_carry()` returns an empty `SceneCarry` if the file is missing — the
expected case for the very first scene of a video.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pipeline.schemas import (
    CarryObject,
    SceneCarry,
    StoryboardScene,
    StyleManifest,
)

logger = logging.getLogger("AEVE")


def carry_path(scenes_dir: Path, scene_id: str) -> Path:
    """Canonical path for a scene's carry file."""
    return Path(scenes_dir) / f"scene_{scene_id}.carry.json"


def read_carry(scenes_dir: Path, scene_id: str) -> SceneCarry:
    """Load `SceneCarry` for `scene_id`, or return an empty one if absent.

    A malformed file logs a warning and is treated as empty — never blocks
    the pipeline.
    """
    path = carry_path(scenes_dir, scene_id)
    if not path.exists():
        return SceneCarry(scene_id=scene_id, objects=[])
    try:
        return SceneCarry.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(
            "[carryover] %s is malformed (%s); treating as empty", path.name, exc
        )
        return SceneCarry(scene_id=scene_id, objects=[])


def write_carry(scenes_dir: Path, carry: SceneCarry) -> Path:
    """Persist a `SceneCarry` to disk and return the path written."""
    path = carry_path(scenes_dir, carry.scene_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(carry.model_dump_json(indent=2), encoding="utf-8")
    return path


def empty_carry(scene_id: str) -> SceneCarry:
    """Convenience: a no-op carry, e.g. for the first scene of a video."""
    return SceneCarry(scene_id=scene_id, objects=[])


# ---------------------------------------------------------------------------
# Predicted carry from storyboard data — used by the orchestrator to give
# scene N+1's Animator a substantive `prior_carry` block without waiting on
# the previous scene's render.
# ---------------------------------------------------------------------------


def _kind_for_name(name: str) -> str:
    """Best-guess Manim primitive for a carry-object name.

    Heuristic: keywords in the name pick the kind. Falls back to `Text`
    because that's the safest default the Animator can place at any zone.
    """
    low = name.lower()
    if any(kw in low for kw in ("eq", "formula", "math", "expr")):
        return "MathTex"
    if "caption" in low or "label" in low:
        return "MarkupText"
    return "Text"


def _zone_for_name(
    name: str, layout_zones: dict[str, tuple[float, float]]
) -> tuple[float, float, float]:
    """Pick a layout zone for a carry-object name.

    The mapping is intentionally conservative: title-ish objects go to the
    title zone, equation-ish objects go to the main zone, caption-ish go to
    the caption zone, anything else lands at the main zone (the default
    "where the action happens" anchor).
    """
    low = name.lower()
    fallback_main = layout_zones.get("main", (0.0, 0.0))
    if "title" in low or "header" in low:
        x, y = layout_zones.get("title", fallback_main)
    elif "caption" in low or "footer" in low:
        x, y = layout_zones.get("caption", fallback_main)
    elif "left" in low:
        x, y = layout_zones.get("left_rail", fallback_main)
    elif "right" in low:
        x, y = layout_zones.get("right_rail", fallback_main)
    else:
        x, y = fallback_main
    return float(x), float(y), 0.0


def predict_carry_from_storyboard(
    prior_scene: StoryboardScene,
    style: StyleManifest,
) -> SceneCarry:
    """Derive a predicted `SceneCarry` for the scene FOLLOWING `prior_scene`.

    Each name in `prior_scene.carryover_objects` is mapped to a kind +
    layout-zone position by the heuristics above. The result is what the
    Animator for scene N+1 will see in its `prior_carry` block.

    The `scene_id` on the returned `SceneCarry` is the prior scene's id —
    that's the convention `read_carry` follows (the file is named
    `scene_<prior_id>.carry.json`).
    """
    objects: list[CarryObject] = []
    for name in prior_scene.carryover_objects:
        if not name or not name.strip():
            continue
        kind = _kind_for_name(name)
        position = _zone_for_name(name, style.layout_zones)
        objects.append(
            CarryObject(name=name.strip(), kind=kind, position=position)
        )
    return SceneCarry(scene_id=prior_scene.scene_id, objects=objects)


__all__ = [
    "carry_path",
    "read_carry",
    "write_carry",
    "empty_carry",
    "predict_carry_from_storyboard",
]
