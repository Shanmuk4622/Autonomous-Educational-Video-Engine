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

from pipeline.schemas import SceneCarry

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


__all__ = ["carry_path", "read_carry", "write_carry", "empty_carry"]
