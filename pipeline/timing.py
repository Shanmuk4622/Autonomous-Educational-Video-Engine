"""
AEVE 2.0 — A/V timing utilities.

Three responsibilities:

  1. ffprobe_duration(path)        — authoritative media duration in seconds.
  2. predict_manim_runtime(code)   — static AST analysis: sum of run_time= and
                                      self.wait(N) literals in construct(). Used
                                      to gate Animator output before render.
  3. pad_or_trim(mp4, target_s)    — post-render correction with ffmpeg tpad /
                                      hard-cut. Drift budget: 50 ms.

All shell-outs use the conda env's ffmpeg / ffprobe via the active PATH (the
project assumes `conda activate cv_conda` per the conda-env feedback memory).

This module replaces the deleted `pipeline/sync_engine.py` and its regex
self.wait() patcher. The contract is now: Animator owns runtime, the AST
predictor enforces it, ffprobe verifies it, ffmpeg pads/trims if needed.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("AEVE")

DRIFT_BUDGET_S = 0.050  # 50 ms — the hard CI gate
DEFAULT_FPS = 30
FFPROBE_TIMEOUT_S = 30
FFMPEG_TIMEOUT_S = 120


# ---------------------------------------------------------------------------
# 1. ffprobe — authoritative duration
# ---------------------------------------------------------------------------


class FfprobeError(RuntimeError):
    pass


def _ffprobe_bin() -> str:
    return shutil.which("ffprobe") or "ffprobe"


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_duration_sync(path: str | Path) -> float:
    """Synchronous duration probe. Returns seconds as float."""
    p = str(path)
    if not os.path.exists(p):
        raise FfprobeError(f"file does not exist: {p}")
    cmd = [
        _ffprobe_bin(),
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        p,
    ]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise FfprobeError("ffprobe not found on PATH (activate cv_conda)") from exc
    except subprocess.TimeoutExpired as exc:
        raise FfprobeError(f"ffprobe timed out on {p}") from exc

    if res.returncode != 0:
        raise FfprobeError(f"ffprobe failed for {p}: {res.stderr.strip()[:300]}")
    out = (res.stdout or "").strip()
    if not out:
        raise FfprobeError(f"ffprobe returned empty duration for {p}")
    try:
        return float(out)
    except ValueError as exc:
        raise FfprobeError(f"ffprobe non-numeric duration {out!r}") from exc


async def ffprobe_duration(path: str | Path) -> float:
    """Async wrapper — runs the synchronous probe in a worker thread."""
    return await asyncio.to_thread(ffprobe_duration_sync, path)


# ---------------------------------------------------------------------------
# 2. Manim runtime predictor — static AST analysis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimePrediction:
    seconds: float
    play_count: int
    wait_count: int
    used_default_play_runtime: int  # play() calls without explicit run_time

    def in_window(self, target_s: float, *, lo: float = 0.92, hi: float = 1.05) -> bool:
        return lo * target_s <= self.seconds <= hi * target_s


# Manim's default run_time when none is given (CE 0.19): 1.0s for self.play().
DEFAULT_PLAY_RUNTIME_S = 1.0


def predict_manim_runtime(code: str) -> RuntimePrediction:
    """Statically estimate construct() runtime from Manim source.

    Sums:
      - `run_time=<literal-or-arithmetic>` keyword args on self.play(...)
      - default 1.0s for self.play(...) calls without run_time
      - literal floats/ints inside self.wait(...)

    Does NOT execute the code. If `code` is unparseable, raises SyntaxError.
    """
    tree = ast.parse(code)
    play_count = 0
    wait_count = 0
    used_default = 0
    total = 0.0

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        method_name = _attr_method_name(node.func)
        if method_name == "play":
            play_count += 1
            kw_runtime = _kw_value(node.keywords, "run_time")
            if kw_runtime is None:
                used_default += 1
                total += DEFAULT_PLAY_RUNTIME_S
            else:
                val = _evaluate_constant(kw_runtime)
                if val is not None:
                    total += val
                else:
                    used_default += 1
                    total += DEFAULT_PLAY_RUNTIME_S
        elif method_name == "wait":
            wait_count += 1
            arg_val = node.args[0] if node.args else None
            kw_val = _kw_value(node.keywords, "duration")
            target = arg_val if arg_val is not None else kw_val
            val = _evaluate_constant(target) if target is not None else 1.0
            total += val if val is not None else 1.0

    return RuntimePrediction(
        seconds=round(total, 4),
        play_count=play_count,
        wait_count=wait_count,
        used_default_play_runtime=used_default,
    )


def _attr_method_name(func: ast.AST) -> str | None:
    """Return 'play' for `self.play`, 'wait' for `self.wait`, else None."""
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id == "self":
            return func.attr
    return None


def _kw_value(keywords: list[ast.keyword], name: str) -> ast.AST | None:
    for kw in keywords:
        if kw.arg == name:
            return kw.value
    return None


def _evaluate_constant(node: ast.AST) -> float | None:
    """Best-effort numeric evaluation of an AST expression.

    Handles literals and simple arithmetic on literals (e.g. `2.5 + 1`,
    `audio_duration - 0.5`). Variables resolve to None — caller falls back to
    the Manim default in that case.
    """
    try:
        compiled = compile(ast.Expression(body=node), "<predict>", "eval")
        value = eval(compiled, {"__builtins__": {}}, {})  # noqa: S307 — sandboxed
        if isinstance(value, (int, float)):
            return float(value)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# 3. pad_or_trim — post-render correction
# ---------------------------------------------------------------------------


@dataclass
class CorrectionResult:
    action: str  # 'noop' | 'padded' | 'trimmed' | 'skipped'
    final_duration_s: float
    delta_s: float


async def pad_or_trim(
    mp4_path: Path,
    target_s: float,
    *,
    fps: int = DEFAULT_FPS,
    drift_budget_s: float = DRIFT_BUDGET_S,
) -> CorrectionResult:
    """Bring an MP4 to within `drift_budget_s` of `target_s`.

    - If shorter: extend last frame via `ffmpeg tpad`.
    - If longer:  hard-cut via `ffmpeg -t target_s`.
    - If within budget: noop.

    Returns a CorrectionResult describing what happened.
    """
    if target_s <= 0:
        return CorrectionResult(action="skipped", final_duration_s=0.0, delta_s=0.0)

    measured = await ffprobe_duration(mp4_path)
    delta = measured - target_s

    if abs(delta) <= drift_budget_s:
        return CorrectionResult(action="noop", final_duration_s=measured, delta_s=delta)

    tmp_path = mp4_path.with_suffix(".corrected.mp4")
    if delta < 0:
        # Video too short — pad with a freeze of the last frame
        pad_seconds = -delta
        cmd = [
            _ffmpeg_bin(), "-y",
            "-i", str(mp4_path),
            "-vf", f"tpad=stop_mode=clone:stop_duration={pad_seconds:.4f}",
            "-r", str(fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "copy",
            str(tmp_path),
        ]
        action = "padded"
    else:
        # Video too long — hard-cut
        cmd = [
            _ffmpeg_bin(), "-y",
            "-i", str(mp4_path),
            "-t", f"{target_s:.4f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "copy",
            str(tmp_path),
        ]
        action = "trimmed"

    res = await asyncio.to_thread(
        subprocess.run, cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_S
    )
    if res.returncode != 0:
        logger.warning(
            "ffmpeg %s failed for %s: %s",
            action,
            mp4_path.name,
            res.stderr.strip()[-300:],
        )
        return CorrectionResult(action="skipped", final_duration_s=measured, delta_s=delta)

    tmp_path.replace(mp4_path)
    final = await ffprobe_duration(mp4_path)
    return CorrectionResult(action=action, final_duration_s=final, delta_s=final - target_s)


# ---------------------------------------------------------------------------
# Convenience: full per-scene timing report (used by the orchestrator)
# ---------------------------------------------------------------------------


@dataclass
class SceneTimingReport:
    audio_duration_s: float
    predicted_runtime_s: float
    measured_video_s: float
    drift_ms: int
    correction: CorrectionResult


async def measure_and_correct(
    mp4_path: Path, audio_duration_s: float, *, fps: int = DEFAULT_FPS
) -> SceneTimingReport:
    """Convenience wrapper used by the renderer + assembler."""
    correction = await pad_or_trim(mp4_path, audio_duration_s, fps=fps)
    drift_ms = int(round(correction.delta_s * 1000))
    return SceneTimingReport(
        audio_duration_s=audio_duration_s,
        predicted_runtime_s=0.0,  # filled by caller from RuntimePrediction
        measured_video_s=correction.final_duration_s,
        drift_ms=drift_ms,
        correction=correction,
    )


__all__ = [
    "CorrectionResult",
    "DEFAULT_PLAY_RUNTIME_S",
    "DRIFT_BUDGET_S",
    "FfprobeError",
    "RuntimePrediction",
    "SceneTimingReport",
    "ffprobe_duration",
    "ffprobe_duration_sync",
    "measure_and_correct",
    "pad_or_trim",
    "predict_manim_runtime",
]
