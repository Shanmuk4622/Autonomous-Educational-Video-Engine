"""
Tests for renderer.healer.

`heal()` is exercised via monkeypatched `call_agent`. `write_fallback_scene`
is tested by checking the rendered Python parses, names the right Scene
class, and lands inside the AST runtime gate band.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from pipeline.animator import RUNTIME_HI, RUNTIME_LO
from pipeline.timing import predict_manim_runtime
from renderer import healer


# ---------------------------------------------------------------------------
# _allocate_runtimes
# ---------------------------------------------------------------------------


def test_allocate_runtimes_sums_close_to_target():
    r = healer._allocate_runtimes(10.0, n_formulas=3)
    # intro + per_formula*3 + emphasis + outro should round-trip near 10s
    total = r["intro_s"] + r["per_formula_s"] * 3 + r["emphasis_s"] + r["outro_s"]
    assert total == pytest.approx(10.0, abs=0.05)


def test_allocate_runtimes_no_formulas_collapses_body_into_per_formula():
    r = healer._allocate_runtimes(8.0, n_formulas=0)
    # Even with 0 formulas, per_formula_s must be > 0 so the no-formula
    # template path has a usable run_time.
    assert r["per_formula_s"] > 0


def test_allocate_runtimes_clamps_below_one_second():
    r = healer._allocate_runtimes(0.1, n_formulas=2)
    # Function clamps to 1.0s minimum so we never emit run_time=0 literals.
    total = r["intro_s"] + r["outro_s"] + r["emphasis_s"] + r["per_formula_s"]
    assert total > 0.5


# ---------------------------------------------------------------------------
# _strip_fences
# ---------------------------------------------------------------------------


def test_strip_fences_python_block():
    assert healer._strip_fences("```python\nx = 1\n```") == "x = 1"


def test_strip_fences_passthrough_when_unfenced():
    assert healer._strip_fences("x = 1\n") == "x = 1\n"


# ---------------------------------------------------------------------------
# write_fallback_scene
# ---------------------------------------------------------------------------


def test_fallback_scene_with_formulas_parses_and_in_band(tmp_path: Path):
    out = tmp_path / "scene_005.py"
    target = 8.0
    healer.write_fallback_scene(
        py_path=out,
        scene_id="005",
        title="Pythagoras",
        formulas=["a^2 + b^2 = c^2", "c = \\sqrt{a^2 + b^2}"],
        target_runtime_s=target,
    )
    src = out.read_text(encoding="utf-8")

    # Parses
    tree = ast.parse(src)
    class_names = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    assert class_names == ["Scene005"]

    # Predicted runtime in the AST gate band
    pred = predict_manim_runtime(src)
    assert pred.in_window(target, lo=RUNTIME_LO, hi=RUNTIME_HI), (
        f"fallback scene predicted={pred.seconds:.3f}s outside band for target {target}"
    )


def test_fallback_scene_without_formulas_still_in_band(tmp_path: Path):
    out = tmp_path / "scene_001.py"
    target = 5.0
    healer.write_fallback_scene(
        py_path=out,
        scene_id="001",
        title="Intro",
        formulas=[],
        target_runtime_s=target,
    )
    src = out.read_text(encoding="utf-8")
    ast.parse(src)
    pred = predict_manim_runtime(src)
    assert pred.in_window(target, lo=RUNTIME_LO, hi=RUNTIME_HI)


def test_fallback_scene_skips_blank_formulas(tmp_path: Path):
    out = tmp_path / "scene_002.py"
    healer.write_fallback_scene(
        py_path=out,
        scene_id="002",
        title="Test",
        formulas=["", "  ", "a^2"],
        target_runtime_s=6.0,
    )
    src = out.read_text(encoding="utf-8")
    # Only the non-blank formula should appear as MathTex(...) instantiation
    # (the `MathTex` token itself also appears in the import line, so we
    # count call sites instead).
    assert src.count("MathTex(") == 1


# ---------------------------------------------------------------------------
# heal() with monkeypatched call_agent
# ---------------------------------------------------------------------------


_GOOD_SCENE_TEMPLATE = '''from manim import FadeIn, FadeOut, Scene, Text

class Scene{scene_id}(Scene):
    def construct(self):
        title = Text("Recovered")
        self.play(FadeIn(title), run_time=2.0)
        self.play(title.animate.scale(1.1), run_time=1.5)
        self.play(title.animate.scale(0.9), run_time=1.0)
        self.play(FadeOut(title), run_time=0.5)
'''


def test_heal_returns_validated_code(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_call_agent(*, role, user_prompt, system_prompt=None, **kwargs):
        assert role == "healer"
        captured["user"] = user_prompt
        captured["system"] = system_prompt or ""
        return _GOOD_SCENE_TEMPLATE.format(scene_id="001")

    monkeypatch.setattr(healer, "call_agent", fake_call_agent)

    out = asyncio.run(
        healer.heal(
            broken_code="def construct(self:\n  oops",
            stderr_tail="SyntaxError: invalid syntax",
            target_runtime_s=5.0,
            scene_id="001",
        )
    )
    assert "Scene001" in out
    assert "FadeOut" in out
    # Stderr tail must reach the LLM via the user prompt
    assert "SyntaxError" in captured["user"]
    # System prompt encodes the contract
    assert "Scene001" in captured["system"]


def test_heal_rejects_unfixable_output(monkeypatch):
    """If the LLM still emits forbidden patterns, heal() raises LLMError."""
    from pipeline.llm_clients.errors import LLMError

    async def fake_call_agent(*, role, user_prompt, system_prompt=None, **kwargs):
        return "def construct(self:\n  still broken"

    monkeypatch.setattr(healer, "call_agent", fake_call_agent)

    with pytest.raises(LLMError, match="rejected by AST gate"):
        asyncio.run(
            healer.heal(
                broken_code="x",
                stderr_tail="boom",
                target_runtime_s=5.0,
                scene_id="001",
            )
        )


def test_heal_strips_fences(monkeypatch):
    """LLM may wrap response in ```python fences — heal must strip."""

    async def fake_call_agent(*, role, user_prompt, system_prompt=None, **kwargs):
        return "```python\n" + _GOOD_SCENE_TEMPLATE.format(scene_id="003") + "\n```"

    monkeypatch.setattr(healer, "call_agent", fake_call_agent)

    out = asyncio.run(
        healer.heal(
            broken_code="x",
            stderr_tail="x",
            target_runtime_s=5.0,
            scene_id="003",
        )
    )
    assert "```" not in out
    assert "Scene003" in out
