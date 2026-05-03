"""
AEVE 2.0 — Phase 5a: Manim renderer + drift correction.

Replaces the legacy `renderer/manim_runner.py` rendering path. Three jobs:

    1. Run Manim CE 0.19 over a generated scene `.py` and produce a silent .mp4.
    2. ffprobe-verify the duration; pad or trim with `pipeline.timing` if the
       drift exceeds the budget (50 ms by default).
    3. Mux the audio onto the corrected video — **NO `-shortest`** — and
       ffprobe-verify one final time.

On nonzero subprocess exit we capture the last 4 KB of stderr (vs. the
legacy 800 chars) and hand it, plus the failing source, to the Healer for one
or more repair rounds. The final fallback is a deterministic Jinja template
so the pipeline NEVER blocks on a single bad scene.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from pipeline.schemas import SceneAudio, SceneCode, SceneVideo
from pipeline.style import StyleManifest
from pipeline.timing import (
    DRIFT_BUDGET_S,
    ffprobe_duration,
    pad_or_trim,
)

logger = logging.getLogger("AEVE")

DEFAULT_FPS = 30
DEFAULT_RESOLUTION = "1920,1080"
MANIM_TIMEOUT_S = 600          # 10 min per scene render
FFMPEG_TIMEOUT_S = 180
STDERR_TAIL_BYTES = 4 * 1024   # last 4 KB on render failure


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RenderError(RuntimeError):
    """Raised when a single Manim subprocess invocation fails.

    Carries the full last-4KB stderr tail so the Healer has full context.
    """

    def __init__(self, message: str, *, stderr_tail: str, returncode: int):
        super().__init__(message)
        self.stderr_tail = stderr_tail
        self.returncode = returncode


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _manim_cmd(
    *,
    py_path: Path,
    class_name: str,
    media_dir: Path,
    output_basename: str,
    fps: int,
    resolution: str,
) -> list[str]:
    """Build the Manim CLI invocation. Uses `python -m manim` so we don't
    depend on the `manim` script being on PATH."""
    return [
        sys.executable,
        "-m",
        "manim",
        "render",
        str(py_path),
        class_name,
        "--fps", str(fps),
        "-r", resolution,
        "--media_dir", str(media_dir),
        "-o", output_basename,
        "--disable_caching",          # avoid cross-run hash collisions
        "--progress_bar", "none",     # progress bars fight stdout on Windows
        "-v", "WARNING",              # less stdout noise
    ]


def _expected_manim_output(
    media_dir: Path,
    py_path: Path,
    output_basename: str,
    *,
    fps: int,
    resolution: str,
) -> Path:
    """Where Manim CE 0.19 writes the rendered .mp4."""
    height = resolution.split(",")[1]
    return (
        media_dir
        / "videos"
        / py_path.stem
        / f"{height}p{fps}"
        / f"{output_basename}.mp4"
    )


def _tail_bytes(text: str | None, *, limit: int = STDERR_TAIL_BYTES) -> str:
    if not text:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text
    return encoded[-limit:].decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Single Manim invocation
# ---------------------------------------------------------------------------


def _run_manim_sync(
    *,
    py_path: Path,
    class_name: str,
    media_dir: Path,
    output_basename: str,
    fps: int,
    resolution: str,
) -> Path:
    """Synchronous: run manim, return the path of the rendered .mp4. Raises
    `RenderError` (with stderr tail) on nonzero exit or missing output."""
    cmd = _manim_cmd(
        py_path=py_path,
        class_name=class_name,
        media_dir=media_dir,
        output_basename=output_basename,
        fps=fps,
        resolution=resolution,
    )
    media_dir.mkdir(parents=True, exist_ok=True)
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=MANIM_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise RenderError(
            f"manim timed out after {MANIM_TIMEOUT_S}s on {py_path.name}",
            stderr_tail=_tail_bytes(getattr(exc, "stderr", None)) or "",
            returncode=-1,
        ) from exc

    if res.returncode != 0:
        tail = _tail_bytes(res.stderr) or _tail_bytes(res.stdout)
        raise RenderError(
            f"manim exited with rc={res.returncode} for {py_path.name}",
            stderr_tail=tail,
            returncode=res.returncode,
        )

    out = _expected_manim_output(
        media_dir, py_path, output_basename, fps=fps, resolution=resolution
    )
    if not out.exists():
        raise RenderError(
            f"manim succeeded but output missing: {out}",
            stderr_tail=_tail_bytes(res.stderr),
            returncode=res.returncode,
        )
    return out


async def _run_manim(**kwargs) -> Path:
    return await asyncio.to_thread(_run_manim_sync, **kwargs)


# ---------------------------------------------------------------------------
# Audio mux — NO -shortest
# ---------------------------------------------------------------------------


def _mux_sync(silent_mp4: Path, mp3: Path, out_mp4: Path) -> None:
    """Mux MP3 onto the silent video. -shortest is FORBIDDEN — it caused the
    legacy A/V truncation."""
    cmd = [
        _ffmpeg_bin(),
        "-y",
        "-i", str(silent_mp4),
        "-i", str(mp3),
        "-map", "0:v",
        "-map", "1:a",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-ac", "2",
        str(out_mp4),
    ]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=FFMPEG_TIMEOUT_S,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"ffmpeg mux failed (rc={res.returncode}): "
            f"{(res.stderr or '').strip()[-300:]}"
        )


async def _mux(silent_mp4: Path, mp3: Path, out_mp4: Path) -> None:
    await asyncio.to_thread(_mux_sync, silent_mp4, mp3, out_mp4)


# ---------------------------------------------------------------------------
# Public API: render with healer
# ---------------------------------------------------------------------------


@dataclass
class RenderConfig:
    fps: int = DEFAULT_FPS
    resolution: str = DEFAULT_RESOLUTION
    drift_budget_s: float = DRIFT_BUDGET_S
    max_attempts: int = 4   # raised from legacy 3


async def render_scene(
    *,
    code: SceneCode,
    audio: SceneAudio,
    video_dir: Path,
    style: StyleManifest | None = None,
    cfg: RenderConfig | None = None,
) -> SceneVideo:
    """Render a single scene end-to-end with healer-aided retry.

    Pipeline:
        for attempt in range(max_attempts):
            try render → break
            except RenderError → call Healer with stderr_tail; rewrite .py
        else:                 → write deterministic fallback scene; render it
        pad_or_trim to match audio duration
        mux MP3 onto silent video (no -shortest)
        ffprobe verify; return SceneVideo

    Raises:
        RuntimeError: pad_or_trim or mux failed catastrophically (rare).
        Never raises on a content-level failure — the deterministic fallback
        guarantees a playable .mp4.
    """
    cfg = cfg or RenderConfig()
    video_dir = Path(video_dir)
    video_dir.mkdir(parents=True, exist_ok=True)

    media_dir = video_dir / "_manim_media"
    output_basename = f"scene_{code.scene_id}_silent"
    silent_mp4 = video_dir / f"{output_basename}.mp4"
    final_mp4 = video_dir / f"scene_{code.scene_id}.mp4"

    # Lazy import — healer.py also imports from this module's siblings, so we
    # break the cycle by importing at call time.
    from renderer.healer import heal, write_fallback_scene

    used_healer = False
    healer_attempts = 0
    last_error: RenderError | None = None
    rendered: Path | None = None

    py_path = code.py_path

    for attempt in range(1, cfg.max_attempts + 1):
        logger.info(
            "[render] scene %s — attempt %d/%d (%s)",
            code.scene_id,
            attempt,
            cfg.max_attempts,
            py_path.name,
        )
        try:
            rendered = await _run_manim(
                py_path=py_path,
                class_name=code.class_name,
                media_dir=media_dir,
                output_basename=output_basename,
                fps=cfg.fps,
                resolution=cfg.resolution,
            )
            if attempt > 1:
                logger.info(
                    "[render] scene %s — recovered on attempt %d",
                    code.scene_id,
                    attempt,
                )
            break
        except RenderError as exc:
            last_error = exc
            healer_attempts += 1
            used_healer = True
            logger.warning(
                "[render] scene %s — attempt %d failed (rc=%d). stderr tail: %s",
                code.scene_id,
                attempt,
                exc.returncode,
                exc.stderr_tail.strip()[-400:],
            )
            # Save the failing source for diagnostics
            bak = py_path.with_suffix(f".attempt_{attempt}.py.bak")
            try:
                bak.write_text(py_path.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:  # pragma: no cover
                pass

            if attempt >= cfg.max_attempts:
                # Final fallback: deterministic scene
                logger.warning(
                    "[render] scene %s — healer exhausted; writing fallback scene",
                    code.scene_id,
                )
                py_path = write_fallback_scene(
                    py_path=py_path,
                    scene_id=code.scene_id,
                    title=f"Scene {code.scene_id}",
                    formulas=[],
                    target_runtime_s=audio.duration_s,
                )
                rendered = await _run_manim(
                    py_path=py_path,
                    class_name=code.class_name,
                    media_dir=media_dir,
                    output_basename=output_basename,
                    fps=cfg.fps,
                    resolution=cfg.resolution,
                )
                break

            # Otherwise, ask the healer to fix the code
            healed_text = await heal(
                broken_code=py_path.read_text(encoding="utf-8"),
                stderr_tail=exc.stderr_tail,
                target_runtime_s=audio.duration_s,
                scene_id=code.scene_id,
                style=style,
            )
            py_path.write_text(healed_text, encoding="utf-8")

    assert rendered is not None  # loop must have either succeeded or fallback'd
    # Move/rename the rendered silent video to a stable location
    if rendered != silent_mp4:
        if silent_mp4.exists():
            silent_mp4.unlink()
        rendered.replace(silent_mp4)

    # Drift correction on the silent video BEFORE mux so the audio always
    # plays the full length we measured.
    correction = await pad_or_trim(
        silent_mp4,
        audio.duration_s,
        fps=cfg.fps,
        drift_budget_s=cfg.drift_budget_s,
    )
    logger.info(
        "[render] scene %s — drift correction: %s (delta=%.3fs)",
        code.scene_id,
        correction.action,
        correction.delta_s,
    )

    # Mux audio (NO -shortest)
    await _mux(silent_mp4, audio.mp3_path, final_mp4)

    measured = await ffprobe_duration(final_mp4)
    drift_ms = int(round((measured - audio.duration_s) * 1000))
    logger.info(
        "[render] scene %s — final %s (measured=%.3fs target=%.3fs drift=%dms)",
        code.scene_id,
        final_mp4.name,
        measured,
        audio.duration_s,
        drift_ms,
    )

    return SceneVideo(
        scene_id=code.scene_id,
        mp4_path=final_mp4,
        measured_duration_s=measured,
        drift_ms=drift_ms,
        used_healer=used_healer,
        healer_attempts=healer_attempts,
    )


__all__ = [
    "DEFAULT_FPS",
    "DEFAULT_RESOLUTION",
    "RenderConfig",
    "RenderError",
    "render_scene",
]
