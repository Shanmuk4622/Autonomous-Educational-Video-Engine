"""
AEVE 2.0 — runtime helpers callable from inside generated Manim scenes.

Generated scenes import from this module to perform side-effects that the
orchestrator can later observe — primarily, writing the per-scene
carryover JSON that records which objects survive to the next scene.

This module is deliberately stdlib-only (no manim import). Generated
scenes pass mobject instances by reference; we duck-type for `.get_center()`
to extract positions. That keeps `pipeline.runtime` import-clean for tests
that don't have manim installed.

Usage in generated code:

    from pipeline.runtime import emit_carry

    class Scene001(Scene):
        def construct(self):
            title = Text("Hello").move_to(TITLE_POS)
            ...
            emit_carry("001", {"title": title})
            self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)

emit_carry is a no-op for the AST runtime predictor — it's not a
`self.play` or `self.wait`, so it doesn't disturb the runtime budget.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Mapping

import config

logger = logging.getLogger("AEVE")


def _safe_position(mobject: Any) -> tuple[float, float, float]:
    """Best-effort (x, y, z) extraction from a Manim VMobject. Falls back
    to (0, 0, 0) if the mobject doesn't expose `.get_center()` (rare but
    possible with custom subclasses)."""
    try:
        center = mobject.get_center()
        return float(center[0]), float(center[1]), float(center[2])
    except Exception:
        return 0.0, 0.0, 0.0


def emit_carry(
    scene_id: str,
    named_mobjects: Mapping[str, Any],
    *,
    output_path: str | Path | None = None,
) -> Path:
    """Write `scene_<id>.carry.json` listing names + kinds + positions.

    Args:
        scene_id: Zero-padded scene id (e.g. "001"). MUST match the
            class name's NNN suffix.
        named_mobjects: Mapping of survival-name → mobject. The next
            scene's Animator can use these names + positions to recreate
            the objects via `.move_to(position)` before its own animations.
        output_path: Override of the output directory. Defaults to
            `config.SCENES_DIR`. Useful for tests + render harnesses.

    Returns:
        Path to the written file.
    """
    base = Path(output_path) if output_path else Path(config.SCENES_DIR)
    base.mkdir(parents=True, exist_ok=True)

    objects = []
    for name, m in named_mobjects.items():
        objects.append(
            {
                "name": str(name),
                "kind": type(m).__name__,
                "position": list(_safe_position(m)),
            }
        )

    payload = {"scene_id": str(scene_id), "objects": objects}
    out = base / f"scene_{scene_id}.carry.json"
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(
        "[runtime] scene %s — wrote %s (%d carry objects)",
        scene_id,
        out.name,
        len(objects),
    )
    return out


__all__ = ["emit_carry"]
