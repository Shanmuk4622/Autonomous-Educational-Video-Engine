"""
AEVE 2.0 — Phase 5b: render-time code Healer.

When a Manim render fails, `renderer.render` hands the failing source +
last-4KB stderr tail to `heal()` here. The healer:

    1. Calls `call_agent(role="healer")` with a tight repair contract:
       broken code in, full Python file out — no commentary, no diff.
    2. Sanitizes the response (legacy-name renames + Polygon spread).
    3. Runs the same AST gates the Animator runs (parse → forbidden →
       class-name → predicted-runtime window).
    4. Returns the cleaned text.

If the gates reject the healed code, `heal()` raises `LLMError` and the
caller (renderer.render) treats it as another render failure — its loop
budget shrinks by one. After `RenderConfig.max_attempts` (default 4),
`renderer.render` calls `write_fallback_scene()` from this module instead,
which renders a deterministic Jinja template — guaranteed to parse and
render.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pipeline.animator import RUNTIME_HI, RUNTIME_LO, run_gates
from pipeline.llm_clients import call_agent
from pipeline.llm_clients.errors import LLMError, LLMErrorContext
from pipeline.style import StyleManifest, manifest_to_prompt_block
from renderer.sanitize import safe_transform

logger = logging.getLogger("AEVE")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "pipeline"

_jinja = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(disabled_extensions=("j2",)),
    trim_blocks=False,
    lstrip_blocks=False,
)


# ---------------------------------------------------------------------------
# System prompt — tight, code-only contract
# ---------------------------------------------------------------------------


def _system_prompt(target_runtime_s: float, scene_id: str) -> str:
    return f"""You are a Python+Manim CE 0.19 debugger.

You will receive:
    - a broken Manim scene (full source)
    - the last 4 KB of the rendering subprocess's stderr
    - the target runtime in seconds

Your job: return a CORRECTED full Python file. Output ONLY the code. No
markdown fences, no diff, no commentary, no explanation.

Hard contract (the AST gate will reject otherwise):
    - One Scene subclass, named `Scene{scene_id}`
    - `from manim import ...` and `from output._style import ...` only
    - No ShowCreation, TextMobject, TexMobject, add_sound, custom shaders
    - Polygon must spread args: Polygon(*pts), never Polygon([pts])
    - Every self.play(...) MUST include a literal `run_time=<float>` kwarg
    - Total of all `run_time=` literals + any `self.wait(N)` literals MUST
      land within [{RUNTIME_LO * target_runtime_s:.3f}s,
      {RUNTIME_HI * target_runtime_s:.3f}s] for target {target_runtime_s:.3f}s
    - End with: self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
    - NO trailing self.wait(N)

Diagnose the stderr, fix the bug, preserve the visual intent. If the
original file is unsalvageable, write a minimal scene that shows the title
and any formulas with FadeIn/FadeOut transitions — but NEVER return empty
code or just an apology.
"""


# ---------------------------------------------------------------------------
# Code extraction
# ---------------------------------------------------------------------------


_FENCE_RE = re.compile(r"^```(?:python|py)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _strip_fences(text: str) -> str:
    m = _FENCE_RE.match(text.strip())
    if m:
        return m.group(1)
    if text.lstrip().startswith("```"):
        return re.sub(r"^```(?:python|py)?\s*\n", "", text.lstrip(), count=1)
    return text


# ---------------------------------------------------------------------------
# Public: heal()
# ---------------------------------------------------------------------------


async def heal(
    *,
    broken_code: str,
    stderr_tail: str,
    target_runtime_s: float,
    scene_id: str,
    style: StyleManifest | None = None,
) -> str:
    """Ask the Healer LLM to fix `broken_code`.

    Returns the cleaned, AST-validated code string. Raises `LLMError` if
    the result fails gate validation (the renderer will then decide whether
    to retry or fall back to a deterministic template).
    """
    style_block = manifest_to_prompt_block(style) if style is not None else ""
    user_prompt = f"""Target runtime: {target_runtime_s:.3f}s
Scene id: {scene_id} (class must be Scene{scene_id})

--- BROKEN CODE ---
{broken_code}
--- END BROKEN CODE ---

--- STDERR TAIL (last 4 KB) ---
{stderr_tail.strip()[-4000:]}
--- END STDERR TAIL ---
{('Visual style contract:' + chr(10) + style_block) if style_block else ''}

Return a corrected full Python file."""

    raw = await call_agent(
        role="healer",
        user_prompt=user_prompt,
        system_prompt=_system_prompt(target_runtime_s, scene_id),
    )
    code = _strip_fences(raw)
    code, sanitize_report = safe_transform(code)

    try:
        outcome = run_gates(code, target_runtime_s, scene_id=scene_id)
    except Exception as exc:
        raise LLMError(
            f"healer output rejected by AST gate: {exc}",
            context=LLMErrorContext(role="healer"),
        ) from exc

    logger.info(
        "[healer] scene %s — accepted (predicted=%.3fs, sanitize=%d)",
        scene_id,
        outcome.predicted_runtime_s,
        sanitize_report.total,
    )
    return outcome.code


# ---------------------------------------------------------------------------
# Public: write_fallback_scene()
# ---------------------------------------------------------------------------


def _python_str_repr(s: str) -> str:
    """Escape a Python string literal safely for Jinja substitution."""
    return repr(s)


def _allocate_runtimes(target_s: float, *, n_formulas: int) -> dict[str, float]:
    """Partition the target into intro / formulas / emphasis / outro slots
    such that the resulting per-play `run_time=` literals sum to target_s."""
    target_s = max(1.0, float(target_s))
    intro = round(0.10 * target_s, 3)
    outro = round(0.05 * target_s, 3)
    emphasis = round(0.15 * target_s, 3)
    body = round(target_s - intro - outro - emphasis, 3)
    if n_formulas <= 0:
        per_formula = round(body, 3)
    else:
        per_formula = round(body / max(1, n_formulas), 3)
    return {
        "intro_s": intro,
        "outro_s": outro,
        "emphasis_s": emphasis,
        "per_formula_s": per_formula,
    }


def write_fallback_scene(
    *,
    py_path: Path,
    scene_id: str,
    title: str,
    formulas: Iterable[str],
    target_runtime_s: float,
) -> Path:
    """Render the deterministic Jinja fallback into `py_path` and return it.

    This path is taken when the healer chain is exhausted. The output is
    guaranteed to parse and pass the AST gates.
    """
    formula_list = [f for f in formulas if f and f.strip()]
    template = _jinja.get_template("fallback_scene.py.j2")
    runtimes = _allocate_runtimes(target_runtime_s, n_formulas=len(formula_list))
    rendered = template.render(
        scene_id=scene_id,
        title_repr=_python_str_repr(title or f"Scene {scene_id}"),
        formula_reprs=[_python_str_repr(f) for f in formula_list],
        formulas=formula_list,
        target_runtime_s=target_runtime_s,
        **runtimes,
    )
    py_path.write_text(rendered, encoding="utf-8")
    logger.info(
        "[healer] wrote deterministic fallback for scene %s -> %s "
        "(formulas=%d, target=%.3fs)",
        scene_id,
        py_path.name,
        len(formula_list),
        target_runtime_s,
    )
    return py_path


__all__ = ["heal", "write_fallback_scene"]
