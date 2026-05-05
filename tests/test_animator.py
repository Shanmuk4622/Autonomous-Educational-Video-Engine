"""
Tests for pipeline.animator — gate logic + monkeypatched end-to-end run.

No live LLM calls. The full `animate()` pipeline is exercised by
monkeypatching `call_agent` to return a hand-crafted scene file.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pipeline import animator as animator_mod
from pipeline.animator import (
    AnimatorGateError,
    _strip_fences,
    animate,
    run_gates,
)
from pipeline.schemas import (
    SceneAudio,
    SceneCarry,
    SceneCode,
    StoryboardScene,
    WordEvent,
)
from pipeline.style import build_style_manifest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _scene(scene_id: str = "001") -> StoryboardScene:
    return StoryboardScene(
        scene_id=scene_id,
        title="Test scene",
        key_concept="Set up a right triangle.",
        narration_draft="A right triangle.",
        formulas=["a^2 + b^2 = c^2"],
        visual_intent="show a triangle",
        layout="title_only",
        carryover_objects=[],
        transition_in="fade",
    )


def _audio(scene_id: str = "001", duration_s: float = 5.0) -> SceneAudio:
    return SceneAudio(
        scene_id=scene_id,
        mp3_path=Path(f"scene_{scene_id}.mp3"),
        duration_s=duration_s,
        word_timeline=[
            WordEvent(word="hello", start_s=0.0, end_s=0.5),
            WordEvent(word="world", start_s=0.5, end_s=1.0),
        ],
        narration_final="Hello world.",
    )


GOOD_CODE_TEMPLATE = '''from manim import FadeIn, FadeOut, Scene, Text

class Scene{scene_id}(Scene):
    def construct(self):
        title = Text("hi")
        self.play(FadeIn(title), run_time=1.5)
        self.play(title.animate.scale(1.1), run_time=2.0)
        self.play(title.animate.scale(0.9), run_time=1.0)
        self.play(FadeOut(title), run_time=0.5)
'''


def _good_code(scene_id: str = "001") -> str:
    return GOOD_CODE_TEMPLATE.format(scene_id=scene_id)


# ---------------------------------------------------------------------------
# _strip_fences
# ---------------------------------------------------------------------------


def test_strip_fences_python():
    text = "```python\nx = 1\n```"
    assert _strip_fences(text) == "x = 1"


def test_strip_fences_no_lang():
    text = "```\nx = 1\n```"
    assert _strip_fences(text) == "x = 1"


def test_strip_fences_passthrough():
    text = "x = 1\n"
    assert _strip_fences(text) == "x = 1\n"


# ---------------------------------------------------------------------------
# run_gates
# ---------------------------------------------------------------------------


def test_gates_accept_good_code():
    code = _good_code()
    outcome = run_gates(code, target_runtime_s=5.0, scene_id="001")
    assert outcome.class_name == "Scene001"
    assert outcome.predicted_runtime_s == pytest.approx(5.0, abs=0.01)


def test_gates_reject_syntax_error():
    code = "def construct(self:\n  oops"
    with pytest.raises(AnimatorGateError, match="syntax error"):
        run_gates(code, target_runtime_s=5.0, scene_id="001")


def test_gates_reject_show_creation():
    code = (
        "from manim import Scene\n"
        "class Scene001(Scene):\n"
        "    def construct(self):\n"
        "        self.play(ShowCreation(c), run_time=5.0)\n"
    )
    with pytest.raises(AnimatorGateError, match="forbidden names"):
        run_gates(code, target_runtime_s=5.0, scene_id="001")


def test_gates_reject_text_mobject():
    code = (
        "from manim import Scene, Text\n"
        "class Scene001(Scene):\n"
        "    def construct(self):\n"
        "        x = TextMobject('hi')\n"
        "        self.play(x.animate.shift(0), run_time=5.0)\n"
    )
    with pytest.raises(AnimatorGateError, match="forbidden names"):
        run_gates(code, target_runtime_s=5.0, scene_id="001")


def test_gates_reject_runtime_too_short():
    code = (
        "from manim import Scene, FadeIn, FadeOut, Text\n"
        "class Scene001(Scene):\n"
        "    def construct(self):\n"
        "        t = Text('hi')\n"
        "        self.play(FadeIn(t), run_time=0.2)\n"
        "        self.play(FadeOut(t), run_time=0.2)\n"
    )
    with pytest.raises(AnimatorGateError, match="outside the acceptable band"):
        run_gates(code, target_runtime_s=10.0, scene_id="001")


def test_gates_reject_runtime_too_long():
    code = (
        "from manim import Scene, FadeIn, FadeOut, Text\n"
        "class Scene001(Scene):\n"
        "    def construct(self):\n"
        "        t = Text('hi')\n"
        "        self.play(FadeIn(t), run_time=20.0)\n"
        "        self.play(FadeOut(t), run_time=10.0)\n"
    )
    with pytest.raises(AnimatorGateError, match="outside the acceptable band"):
        run_gates(code, target_runtime_s=5.0, scene_id="001")


def test_gates_reject_wrong_class_name():
    code = (
        "from manim import Scene, FadeIn, FadeOut, Text\n"
        "class WrongName(Scene):\n"
        "    def construct(self):\n"
        "        t = Text('hi')\n"
        "        self.play(FadeIn(t), run_time=2.5)\n"
        "        self.play(FadeOut(t), run_time=2.5)\n"
    )
    with pytest.raises(AnimatorGateError, match="Scene001"):
        run_gates(code, target_runtime_s=5.0, scene_id="001")


def test_gates_reject_no_scene_subclass():
    code = "x = 1\n"
    with pytest.raises(AnimatorGateError, match="no Scene subclass"):
        run_gates(code, target_runtime_s=5.0, scene_id="001")


def test_gates_reject_empty_code():
    with pytest.raises(AnimatorGateError, match="empty"):
        run_gates("   \n  ", target_runtime_s=5.0, scene_id="001")


# ---------------------------------------------------------------------------
# animate() end-to-end with monkeypatched call_agent
# ---------------------------------------------------------------------------


def test_animate_first_attempt_succeeds(monkeypatch, tmp_path: Path):
    style = build_style_manifest()

    async def fake_call_agent(*, role, user_prompt, system_prompt=None, **kwargs):
        assert role == "animator"
        assert "target_runtime_s" not in user_prompt or "Target runtime" in user_prompt
        return _good_code("001")

    monkeypatch.setattr(animator_mod, "call_agent", fake_call_agent)

    code = asyncio.run(
        animate(
            scene=_scene("001"),
            audio=_audio("001", duration_s=5.0),
            prior_carry=None,
            style=style,
            scenes_dir=tmp_path,
        )
    )

    assert isinstance(code, SceneCode)
    assert code.scene_id == "001"
    assert code.class_name == "Scene001"
    assert code.ast_validated is True
    assert code.predicted_runtime_s == pytest.approx(5.0, abs=0.01)
    assert code.py_path == tmp_path / "scene_001.py"
    assert code.py_path.exists()


def test_animate_repair_round_recovers(monkeypatch, tmp_path: Path):
    """First call returns broken code → animate retries with error injection.

    We use a runtime-window failure (run_time literals too small) since the
    sanitizer would auto-fix legacy-name failures before they hit the gate.
    """
    calls: list[str] = []
    style = build_style_manifest()

    async def fake_call_agent(*, role, user_prompt, system_prompt=None, **kwargs):
        calls.append(user_prompt)
        if len(calls) == 1:
            # Total run_time = 0.4s; target is 5.0s → out of band
            return (
                "from manim import Scene, FadeIn, FadeOut, Text\n"
                "class Scene001(Scene):\n"
                "    def construct(self):\n"
                "        t = Text('hi')\n"
                "        self.play(FadeIn(t), run_time=0.2)\n"
                "        self.play(FadeOut(t), run_time=0.2)\n"
            )
        return _good_code("001")

    monkeypatch.setattr(animator_mod, "call_agent", fake_call_agent)

    code = asyncio.run(
        animate(
            scene=_scene("001"),
            audio=_audio("001", duration_s=5.0),
            prior_carry=None,
            style=style,
            scenes_dir=tmp_path,
        )
    )
    assert isinstance(code, SceneCode)
    assert len(calls) == 2  # first attempt failed, repair round succeeded
    # Repair round prompt must include the gate failure
    assert "previous attempt failed gate validation" in calls[1]
    assert "outside the acceptable band" in calls[1]
    # Backup of the failing attempt must exist
    bak = tmp_path / "scene_001.attempt_1.py.bak"
    assert bak.exists()


def test_animate_two_failures_fall_back_to_deterministic(monkeypatch, tmp_path: Path):
    """If both attempts fail gates, animate() writes the deterministic fallback
    scene (via renderer.healer.write_fallback_scene) rather than raising. The
    pipeline must not block on a single scene's animation failure."""
    style = build_style_manifest()

    async def fake_call_agent(*, role, user_prompt, system_prompt=None, **kwargs):
        return "def construct(self:\n  syntax-error"

    monkeypatch.setattr(animator_mod, "call_agent", fake_call_agent)

    scene_code = asyncio.run(
        animate(
            scene=_scene("001"),
            audio=_audio("001", duration_s=5.0),
            prior_carry=None,
            style=style,
            scenes_dir=tmp_path,
        )
    )

    # Returned SceneCode points at a file that parses + passes gates.
    assert scene_code.scene_id == "001"
    assert scene_code.class_name == "Scene001"
    assert scene_code.ast_validated is True
    assert scene_code.target_runtime_s == 5.0
    assert scene_code.py_path.exists()

    # Both failing attempts left .bak files for diagnostics.
    bak1 = tmp_path / "scene_001.attempt_1.py.bak"
    bak2 = tmp_path / "scene_001.attempt_2.py.bak"
    assert bak1.exists()
    assert bak2.exists()

    # The fallback file's body uses the storyboard's title (deterministic
    # Jinja signature). Confirms we used write_fallback_scene, not the
    # broken LLM output.
    body = scene_code.py_path.read_text(encoding="utf-8")
    assert "class Scene001" in body
    assert "FadeOut" in body
    # The fallback partitions runtime via _allocate_runtimes — predicted must
    # land in-band by construction.
    assert 0.92 * 5.0 <= scene_code.predicted_runtime_s <= 1.05 * 5.0


def test_animate_uses_carry_when_provided(monkeypatch, tmp_path: Path):
    """Prior-carry block must appear in the user prompt verbatim."""
    from pipeline.schemas import CarryObject

    style = build_style_manifest()
    captured: dict[str, str] = {}

    async def fake_call_agent(*, role, user_prompt, system_prompt=None, **kwargs):
        captured["user"] = user_prompt
        return _good_code("002")

    monkeypatch.setattr(animator_mod, "call_agent", fake_call_agent)

    carry = SceneCarry(
        scene_id="002",
        objects=[
            CarryObject(name="title", kind="Text", position=(0.0, 3.2, 0.0)),
        ],
    )

    asyncio.run(
        animate(
            scene=_scene("002"),
            audio=_audio("002", duration_s=5.0),
            prior_carry=carry,
            style=style,
            scenes_dir=tmp_path,
        )
    )

    assert "title: Text at (0.00, 3.20, 0.00)" in captured["user"]
