"""
AEVE 2.0 — layout template loader.

Six fixed Manim scene skeletons. The Director picks one per scene; the
Animator receives the matching template body as a structural guide and
generates a final scene that:
    - keeps the import block, the layout-zone usage, the cleanup tail
    - replaces placeholder titles/equations/narration with real content
    - preserves the explicit `run_time=` literal pattern (so the AST
      runtime predictor can sum a hard total)
    - never invents new coordinates outside the layout zones

Templates live next to this file as `<layout>.py` plain-Python files. They
are loaded as text at runtime; we deliberately do NOT use Jinja for Day 4
(no parameterization required — the LLM does the substitution itself).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import get_args

from pipeline.schemas import LayoutTemplate

_TEMPLATES_DIR: Path = Path(__file__).parent

LAYOUT_NAMES: tuple[str, ...] = get_args(LayoutTemplate)


@lru_cache(maxsize=None)
def load_template(layout: LayoutTemplate) -> str:
    """Return the raw Python text of the template for `layout`.

    Cached on first read.
    """
    if layout not in LAYOUT_NAMES:
        raise ValueError(
            f"unknown layout {layout!r}; expected one of {LAYOUT_NAMES}"
        )
    path = _TEMPLATES_DIR / f"{layout}.py"
    if not path.exists():
        raise FileNotFoundError(f"template file missing: {path}")
    return path.read_text(encoding="utf-8")


def template_path(layout: LayoutTemplate) -> Path:
    """Path to a layout template file (does not check existence)."""
    return _TEMPLATES_DIR / f"{layout}.py"


def all_layouts() -> tuple[str, ...]:
    return LAYOUT_NAMES


__all__ = ["load_template", "template_path", "all_layouts", "LAYOUT_NAMES"]
