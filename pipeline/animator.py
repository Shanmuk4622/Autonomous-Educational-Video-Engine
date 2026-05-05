"""
AEVE 2.0 — Phase 4: Animator.

Takes a `StoryboardScene`, the matching `SceneAudio` (which carries the
ffprobe-measured target runtime), and a `SceneCarry` from the prior scene,
and emits a `SceneCode` — a ready-to-render Manim scene file whose construct()
total runtime lands within ±5%/-8% of the audio duration.

Routing (see registry.ROUTES["animator"]):
    primary    — OpenRouter nvidia/nemotron-3-coder
    fallback 1 — OpenRouter qwen/qwen3-coder
    fallback 2 — OpenRouter deepseek/deepseek-chat-v3

Robustness:
    1. `renderer.sanitize.safe_transform` rewrites legacy names BEFORE gates.
    2. Gate A — `ast.parse(code)` (syntax check).
    3. Gate B — forbidden-name walk (ShowCreation, TextMobject, TexMobject,
       add_sound). Ensures we don't render something the renderer can't.
    4. Gate C — `predict_manim_runtime(code)` ∈ [0.92·T, 1.05·T].
    5. On any gate failure, ONE repair round is attempted with the gate error
       injected back into the user prompt. After two attempts we fall back to
       `renderer.healer.write_fallback_scene` — a deterministic Jinja scene
       guaranteed to pass the gates. The pipeline never blocks on a single
       scene's animation failure. Network/rate-limit failures are handled
       inside `call_agent` — we only own the gate-repair loop.

Allowed primitives are listed verbatim in the system prompt; the AST walker
also rejects anything starting with `add_sound` or matching the forbidden set.
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import config

from pipeline.carryover import empty_carry
from pipeline.llm_clients import call_agent
from pipeline.schemas import (
    SceneAudio,
    SceneCarry,
    SceneCode,
    StoryboardScene,
    StyleManifest,
)
from pipeline.style import manifest_to_prompt_block
from pipeline.templates import load_template
from pipeline.timing import predict_manim_runtime
from renderer.sanitize import safe_transform

logger = logging.getLogger("AEVE")


FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "ShowCreation",
        "TextMobject",
        "TexMobject",
        "add_sound",
    }
)

ALLOWED_PRIMITIVES: tuple[str, ...] = (
    "Text",
    "MathTex",
    "Tex",
    "MarkupText",
    "VGroup",
    "Axes",
    "NumberPlane",
    "FunctionGraph",
    "Arrow",
    "Dot",
    "Line",
    "Circle",
    "Square",
    "Rectangle",
    "RoundedRectangle",
    "Polygon",
    "BraceLabel",
    "SurroundingRectangle",
    "Code",
)

# Runtime gate band (matches RuntimePrediction.in_window default)
RUNTIME_LO = 0.92
RUNTIME_HI = 1.05


# ---------------------------------------------------------------------------
# Gate exceptions
# ---------------------------------------------------------------------------


class AnimatorGateError(ValueError):
    """A gate (parse / forbidden-name / runtime-window) rejected the output."""


@dataclass
class GateOutcome:
    code: str
    class_name: str
    predicted_runtime_s: float


# ---------------------------------------------------------------------------
# System prompt — verbatim contract
# ---------------------------------------------------------------------------


def _system_prompt(target_runtime_s: float) -> str:
    return f"""You are a Manim CE 0.19 expert. Output ONE complete Python file
implementing a single `Scene` subclass that animates the given storyboard
scene. Output ONLY the code — no markdown fences, no commentary.

REQUIRED imports (the renderer auto-generates `output/_style.py` per run):
    from manim import *
    from output._style import *

ALLOWED primitives (no others):
    {", ".join(ALLOWED_PRIMITIVES)}

FORBIDDEN names (will be rejected by the AST gate):
    {", ".join(sorted(FORBIDDEN_NAMES))}, custom shaders, third-party imports,
    Polygon([list]) — must spread coords as Polygon(*list), raw LaTeX inside
    Text(...) — use MathTex(...) for math.

LAYOUT DISCIPLINE:
    Place every VMobject inside one of the layout zones imported from
    output._style: TITLE_POS, MAIN_POS, CAPTION_POS, LEFT_RAIL_POS,
    RIGHT_RAIL_POS, FOOTER_POS. Anchor with `.move_to(<ZONE>)` or `.next_to`
    an existing anchor. Never invent free-floating coordinates.

COLOR PALETTE (use these, no others):
    BG, PRIMARY, ACCENT, MUTED, SUCCESS, WARN

PACING BUDGET (must sum to {target_runtime_s:.3f}s ±5%):
    ~10% intro / ~70% derivation / ~15% emphasis / ~5% transition out.
    Every self.play(...) MUST include an explicit literal `run_time=<float>`
    kwarg (e.g. run_time=2.5). NEVER pass a variable for run_time. Literal
    arithmetic on numbers (e.g. 1.5 + 0.5) is allowed.

REQUIRED ENDING:
    self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
    NO trailing self.wait(N). NO black-screen padding.

CARRY-OUT (optional but recommended when `carryover_objects` is non-empty):
    Just BEFORE the final FadeOut, call:

        from pipeline.runtime import emit_carry
        emit_carry("<scene_id>", {{"name1": mob1, "name2": mob2}})

    This writes `output/scenes/scene_<scene_id>.carry.json` so the next
    scene's Animator can place the same objects at their final positions.
    The names you pass MUST match the names in the storyboard's
    `carryover_objects` list. emit_carry is NOT a self.play/self.wait
    call — it does not consume any of the runtime budget.

CONTINUITY:
    The "Prior carry" block lists named objects from the previous scene that
    you MUST treat as already on screen. If a formula reappears, prefer
    `ReplacementTransform(prior, new)` over re-creating it.

Return a complete `.py` file. The class name MUST be `Scene<NNN>` where NNN
is the zero-padded scene_id (e.g. `Scene001`, `Scene042`).
"""


def _user_prompt(
    scene: StoryboardScene,
    audio: SceneAudio,
    prior_carry: SceneCarry,
    style: StyleManifest,
    target_runtime_s: float,
) -> str:
    timeline_block = "\n".join(
        f"  {i + 1:>3}. [{w.start_s:6.3f}–{w.end_s:6.3f}s] {w.word}"
        for i, w in enumerate(audio.word_timeline[:60])
    )
    if len(audio.word_timeline) > 60:
        timeline_block += f"\n  … ({len(audio.word_timeline) - 60} more words)"

    carry_block = "(none — this is the first scene)"
    if prior_carry.objects:
        carry_block = "\n".join(
            f"  - {o.name}: {o.kind} at ({o.position[0]:.2f}, "
            f"{o.position[1]:.2f}, {o.position[2]:.2f})"
            for o in prior_carry.objects
        )

    template_body = load_template(scene.layout)

    return f"""Scene metadata:
  scene_id:        {scene.scene_id}
  title:           {scene.title}
  key_concept:     {scene.key_concept}
  layout:          {scene.layout}
  transition_in:   {scene.transition_in}
  formulas:        {scene.formulas}
  visual_intent:   {scene.visual_intent}

Spoken narration (already TTS'd; this is what the audience hears):
  "{audio.narration_final}"

Target runtime (ffprobe-measured from MP3): {target_runtime_s:.3f}s
Acceptable AST-predicted band: [{RUNTIME_LO * target_runtime_s:.3f}s,
{RUNTIME_HI * target_runtime_s:.3f}s].

Word-level timeline (anchors for reveals):
{timeline_block}

Prior carry (objects already on screen at scene start):
{carry_block}

Visual style contract (follow palette + layout zones):
{manifest_to_prompt_block(style)}

Layout skeleton ({scene.layout}) — follow this structure, replace placeholders:
```python
{template_body}
```

Now write a complete `.py` file with class name `Scene{scene.scene_id}`.
The total of all `run_time=` literals + any `self.wait(N)` calls MUST land in
the acceptable band above.
"""


# ---------------------------------------------------------------------------
# Code extraction (strip ``` fences)
# ---------------------------------------------------------------------------


_FENCE_RE = re.compile(r"^```(?:python|py)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _strip_fences(text: str) -> str:
    m = _FENCE_RE.match(text.strip())
    if m:
        return m.group(1)
    # Sometimes the model puts a fence only at the start, no closing
    if text.lstrip().startswith("```"):
        return re.sub(r"^```(?:python|py)?\s*\n", "", text.lstrip(), count=1)
    return text


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def _find_scene_class(tree: ast.Module) -> ast.ClassDef:
    """Return the (single) Scene subclass in the module."""
    candidates: list[ast.ClassDef] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = (
                    base.id
                    if isinstance(base, ast.Name)
                    else getattr(base, "attr", None)
                )
                if base_name == "Scene":
                    candidates.append(node)
                    break
    if not candidates:
        # Fall back to any ClassDef
        candidates = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    if not candidates:
        raise AnimatorGateError("no Scene subclass found in animator output")
    if len(candidates) > 1:
        raise AnimatorGateError(
            f"animator output declares {len(candidates)} Scene subclasses; "
            "expected exactly one"
        )
    return candidates[0]


def _scan_forbidden(tree: ast.Module) -> list[str]:
    """Return a list of forbidden-name violations (empty if clean)."""
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            violations.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            violations.append(node.attr)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES:
                    violations.append(alias.name)
    return violations


def run_gates(code: str, target_runtime_s: float, *, scene_id: str) -> GateOutcome:
    """Apply all three gates. Raises AnimatorGateError on any failure."""
    if not code.strip():
        raise AnimatorGateError("animator returned empty code")

    # Gate A — syntax
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise AnimatorGateError(
            f"syntax error at line {exc.lineno}: {exc.msg}"
        ) from exc

    # Gate B — forbidden names
    violations = _scan_forbidden(tree)
    if violations:
        unique = sorted(set(violations))
        raise AnimatorGateError(
            f"forbidden names used: {unique}. "
            "Replace ShowCreation→Create, TextMobject→Text, TexMobject→MathTex; "
            "remove add_sound (audio is muxed externally)."
        )

    # Class name
    scene_class = _find_scene_class(tree)
    expected = f"Scene{scene_id}"
    if scene_class.name != expected:
        raise AnimatorGateError(
            f"scene class is named {scene_class.name!r}; expected {expected!r}."
        )

    # Gate C — predicted runtime
    pred = predict_manim_runtime(code)
    if not pred.in_window(target_runtime_s, lo=RUNTIME_LO, hi=RUNTIME_HI):
        raise AnimatorGateError(
            f"predicted runtime {pred.seconds:.3f}s is outside the acceptable "
            f"band [{RUNTIME_LO * target_runtime_s:.3f}s, "
            f"{RUNTIME_HI * target_runtime_s:.3f}s] for target "
            f"{target_runtime_s:.3f}s. Adjust `run_time=` literals so the sum "
            "lands in-band. Spread reveals across the full duration; do NOT "
            "pad with self.wait(N) at the end."
        )

    return GateOutcome(
        code=code,
        class_name=scene_class.name,
        predicted_runtime_s=pred.seconds,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def animate(
    *,
    scene: StoryboardScene,
    audio: SceneAudio,
    prior_carry: SceneCarry | None = None,
    style: StyleManifest,
    scenes_dir: Path | None = None,
) -> SceneCode:
    """Generate, sanitize, gate, and persist a Manim scene file.

    Args:
        scene: The Director's StoryboardScene.
        audio: SceneAudio with ffprobe-measured `duration_s` (the runtime target).
        prior_carry: Carryover from scene N-1; pass `None` for the first scene.
        style: The StyleManifest (palette + layout zones).
        scenes_dir: Where to write the .py file. Defaults to config.SCENES_DIR.

    Returns:
        SceneCode pointing at the written file. If both gate-repair attempts
        fail, a deterministic fallback scene is written instead — the function
        always returns a valid SceneCode rather than raising.
    """
    target_runtime_s = audio.duration_s
    carry = prior_carry or empty_carry(scene.scene_id)
    out_dir = Path(scenes_dir) if scenes_dir else Path(config.SCENES_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"scene_{scene.scene_id}.py"

    sys_prompt = _system_prompt(target_runtime_s)
    base_user_prompt = _user_prompt(scene, audio, carry, style, target_runtime_s)

    last_gate_error: AnimatorGateError | None = None

    for attempt in range(2):
        if attempt == 0:
            user_prompt = base_user_prompt
        else:
            assert last_gate_error is not None
            user_prompt = (
                base_user_prompt
                + "\n\nYour previous attempt failed gate validation:\n  "
                + str(last_gate_error)
                + "\n\nReturn a corrected file that passes the gate. "
                "Output ONLY the Python code."
            )

        logger.info(
            "[animator] scene %s — attempt %d (target=%.3fs, layout=%s)",
            scene.scene_id,
            attempt + 1,
            target_runtime_s,
            scene.layout,
        )
        raw = await call_agent(
            role="animator",
            user_prompt=user_prompt,
            system_prompt=sys_prompt,
        )
        code = _strip_fences(raw)
        code, sanitize_report = safe_transform(code)
        try:
            outcome = run_gates(
                code, target_runtime_s, scene_id=scene.scene_id
            )
        except AnimatorGateError as gate_exc:
            last_gate_error = gate_exc
            logger.warning(
                "[animator] scene %s — attempt %d gate failed: %s",
                scene.scene_id,
                attempt + 1,
                gate_exc,
            )
            # Save the failing source for debugging
            fail_path = out_dir / f"scene_{scene.scene_id}.attempt_{attempt + 1}.py.bak"
            try:
                fail_path.write_text(code, encoding="utf-8")
            except OSError:  # pragma: no cover
                pass
            continue

        out_path.write_text(outcome.code, encoding="utf-8")
        logger.info(
            "[animator] scene %s — wrote %s (predicted=%.3fs, target=%.3fs, "
            "sanitize=%d transforms)",
            scene.scene_id,
            out_path.name,
            outcome.predicted_runtime_s,
            target_runtime_s,
            sanitize_report.total,
        )
        return SceneCode(
            scene_id=scene.scene_id,
            py_path=out_path,
            class_name=outcome.class_name,
            target_runtime_s=target_runtime_s,
            ast_validated=True,
            predicted_runtime_s=outcome.predicted_runtime_s,
        )

    # Gate-repair exhausted. Fall back to the deterministic Jinja scene rather
    # than abort the whole pipeline — matches how renderer.render handles its
    # own exhaustion. The fallback uses the storyboard's title + formulas and
    # is guaranteed to parse and pass the same gates.
    from renderer.healer import write_fallback_scene  # deferred: avoids cycle

    logger.warning(
        "[animator] scene %s — gate-repair exhausted (%s); writing "
        "deterministic fallback scene",
        scene.scene_id,
        last_gate_error,
    )
    write_fallback_scene(
        py_path=out_path,
        scene_id=scene.scene_id,
        title=scene.title,
        formulas=scene.formulas,
        target_runtime_s=target_runtime_s,
    )
    fallback_code = out_path.read_text(encoding="utf-8")
    outcome = run_gates(fallback_code, target_runtime_s, scene_id=scene.scene_id)
    return SceneCode(
        scene_id=scene.scene_id,
        py_path=out_path,
        class_name=outcome.class_name,
        target_runtime_s=target_runtime_s,
        ast_validated=True,
        predicted_runtime_s=outcome.predicted_runtime_s,
    )


__all__ = [
    "ALLOWED_PRIMITIVES",
    "AnimatorGateError",
    "FORBIDDEN_NAMES",
    "RUNTIME_HI",
    "RUNTIME_LO",
    "animate",
    "run_gates",
]
