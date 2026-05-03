"""
Tests for pipeline.templates — every layout file must:
  - exist on disk
  - parse as valid Python (ast.parse)
  - import from manim and from output._style
  - mention at least one zone constant from output._style (TITLE_POS, etc.)
  - end with a FadeOut play (no trailing self.wait padding)
"""

from __future__ import annotations

import ast
from typing import get_args

import pytest

from pipeline.schemas import LayoutTemplate
from pipeline.templates import all_layouts, load_template, template_path


LAYOUTS = list(get_args(LayoutTemplate))
ZONE_CONSTS = (
    "TITLE_POS",
    "MAIN_POS",
    "CAPTION_POS",
    "LEFT_RAIL_POS",
    "RIGHT_RAIL_POS",
    "FOOTER_POS",
)


def test_all_layouts_advertised():
    advertised = set(all_layouts())
    expected = set(LAYOUTS)
    assert advertised == expected


@pytest.mark.parametrize("layout", LAYOUTS)
def test_template_file_exists(layout):
    assert template_path(layout).exists(), f"missing template: {layout}.py"


@pytest.mark.parametrize("layout", LAYOUTS)
def test_template_parses(layout):
    src = load_template(layout)
    ast.parse(src)  # raises SyntaxError on failure


@pytest.mark.parametrize("layout", LAYOUTS)
def test_template_imports_manim_and_style(layout):
    src = load_template(layout)
    assert "from manim" in src, f"{layout}: missing `from manim` import"
    assert "from output._style" in src, f"{layout}: missing style import"


@pytest.mark.parametrize("layout", LAYOUTS)
def test_template_uses_layout_zone(layout):
    src = load_template(layout)
    assert any(z in src for z in ZONE_CONSTS), (
        f"{layout}: no layout-zone constant referenced"
    )


@pytest.mark.parametrize("layout", LAYOUTS)
def test_template_no_trailing_wait_pad(layout):
    """The contract forbids final self.wait(N) padding."""
    src = load_template(layout)
    # Check the final non-empty line
    lines = [line for line in src.strip().splitlines() if line.strip()]
    last = lines[-1].strip()
    assert "self.wait" not in last, (
        f"{layout}: ends with self.wait — forbidden by Animator contract"
    )


@pytest.mark.parametrize("layout", LAYOUTS)
def test_template_has_fadeout_cleanup(layout):
    src = load_template(layout)
    assert "FadeOut" in src, f"{layout}: missing FadeOut cleanup play"


def test_load_template_unknown_raises():
    with pytest.raises(ValueError, match="unknown layout"):
        load_template("nonexistent_layout")  # type: ignore[arg-type]
