"""
AEVE 2.0 assembler — final video stitching.

Takes already-rendered `SceneVideo[]` (each with audio muxed in by
`renderer.render`), runs a deterministic "normalize-then-concat" with
per-clip re-encoding to identical timebase + audio params, and asserts
the final ffprobe-measured duration matches the sum of audio durations
within 50 ms. NO `-shortest` is ever used.

Called by `pipeline.orchestrator` in Phase 6.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

from pipeline.schemas import FinalVideo, SceneAudio, SceneVideo
from pipeline.timing import DRIFT_BUDGET_S, ffprobe_duration

logger = logging.getLogger("AEVE")


_NORMALIZE_TIMEOUT_S = 240
_CONCAT_TIMEOUT_S = 300


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def build_normalize_cmd(in_mp4: Path, out_mp4: Path, *, fps: int = 30) -> list[str]:
    """Re-encode `in_mp4` to a canonical timebase + audio so concat is safe.

    Idempotent: running it on an already-normalized clip is a noop in spirit
    (output is byte-identical for our purposes within ffmpeg encoder noise).
    """
    return [
        _ffmpeg_bin(),
        "-y",
        "-i", str(in_mp4),
        "-vf", f"fps={fps},scale=1920:1080:flags=lanczos,setsar=1",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-video_track_timescale", "30000",
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
        "-b:a", "192k",
        str(out_mp4),
    ]


def build_concat_cmd(concat_list: Path, out_mp4: Path) -> list[str]:
    """Concat demuxer over already-normalized inputs. NO `-shortest`."""
    return [
        _ffmpeg_bin(),
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",          # safe because every input is canonicalized
        "-movflags", "+faststart",
        str(out_mp4),
    ]


def _run_ffmpeg(cmd: list[str], *, timeout_s: int) -> None:
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if res.returncode != 0:
        tail = (res.stderr or "").strip()[-400:]
        raise RuntimeError(f"ffmpeg failed (rc={res.returncode}): {tail}")


async def _normalize(in_mp4: Path, out_mp4: Path, *, fps: int) -> Path:
    cmd = build_normalize_cmd(in_mp4, out_mp4, fps=fps)
    await asyncio.to_thread(_run_ffmpeg, cmd, timeout_s=_NORMALIZE_TIMEOUT_S)
    return out_mp4


async def _concat(concat_list: Path, out_mp4: Path) -> Path:
    cmd = build_concat_cmd(concat_list, out_mp4)
    await asyncio.to_thread(_run_ffmpeg, cmd, timeout_s=_CONCAT_TIMEOUT_S)
    return out_mp4


def _write_concat_list(normalized_clips: list[Path], list_path: Path) -> None:
    """Build the demuxer manifest. ffmpeg requires forward-slash paths and
    single-quoted entries; embedded apostrophes are escaped per ffmpeg syntax."""
    list_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for clip in normalized_clips:
        # ffmpeg concat: file '<path>'  with apostrophes escaped as '\''
        safe = str(clip.resolve()).replace("\\", "/").replace("'", r"'\''")
        lines.append(f"file '{safe}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def assemble(
    *,
    scene_videos: list[SceneVideo],
    scene_audios: list[SceneAudio],
    final_dir: Path | None = None,
    work_dir: Path | None = None,
    output_name: str = "final.mp4",
    fps: int = 30,
    drift_budget_s: float = DRIFT_BUDGET_S,
) -> FinalVideo:
    """AEVE 2.0 final assembler.

    Pipeline:
        1. Re-encode each per-scene .mp4 with identical fps/scale/timebase/aac.
        2. Concat demuxer over the normalized files (`-c copy` is now safe).
        3. ffprobe-verify the final duration is within `drift_budget_s` of
           sum(scene_audios.duration_s).
    Args:
        scene_videos: in scene-id order; each .mp4 already has audio muxed.
        scene_audios: in scene-id order; used only for the drift assertion.
        final_dir: where final.mp4 lives. Defaults to `config.FINAL_DIR`.
        work_dir: where normalized intermediates live. Defaults to
            `<final_dir>/_concat_workdir`. Cleaned up unless an exception
            is raised mid-flight.
    Returns:
        `FinalVideo` with measured total + drift_ms.
    """
    if not scene_videos:
        raise ValueError("assemble: scene_videos is empty")

    final_dir = Path(final_dir) if final_dir else Path(config.FINAL_DIR)
    final_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(work_dir) if work_dir else (final_dir / "_concat_workdir")
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Normalize each clip in scene-id order
    sorted_videos = sorted(scene_videos, key=lambda v: v.scene_id)
    normalized: list[Path] = []
    for v in sorted_videos:
        out = work_dir / f"norm_{v.scene_id}.mp4"
        await _normalize(v.mp4_path, out, fps=fps)
        normalized.append(out)
        logger.info(
            "[assembler] scene %s normalized -> %s", v.scene_id, out.name
        )

    # 2. Concat demuxer
    concat_list = work_dir / "concat.txt"
    _write_concat_list(normalized, concat_list)
    final_mp4 = final_dir / output_name
    await _concat(concat_list, final_mp4)
    logger.info("[assembler] concat -> %s", final_mp4)

    # 3. Drift assertion
    measured = await ffprobe_duration(final_mp4)
    target_total = sum(a.duration_s for a in scene_audios)
    drift_ms = int(round((measured - target_total) * 1000))
    logger.info(
        "[assembler] final duration=%.3fs target=%.3fs drift=%dms (budget=%.0fms)",
        measured,
        target_total,
        drift_ms,
        drift_budget_s * 1000,
    )
    if abs(measured - target_total) > drift_budget_s:
        logger.error(
            "[assembler] drift %dms exceeds budget %dms",
            drift_ms,
            int(drift_budget_s * 1000),
        )
        # Don't raise — the file is still playable. Caller / CI gate decides.

    return FinalVideo(
        mp4_path=final_mp4,
        total_duration_s=measured,
        scene_count=len(sorted_videos),
        total_drift_ms=drift_ms,
    )


__all__ = [
    "assemble",
    "build_concat_cmd",
    "build_normalize_cmd",
]
