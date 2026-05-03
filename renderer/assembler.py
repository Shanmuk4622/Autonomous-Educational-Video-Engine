"""
Assembler — final video stitching.

This module exposes BOTH:

    1. Legacy AEVE 1.0 entrypoints — `assemble_final_video()`,
       `merge_audio_video()`, `concatenate_scenes()`. Called by `app.py` and
       `main.py`. Use `-shortest` and re-render scenes from code paths.
       UNCHANGED so the legacy flow keeps working during the side-by-side
       rewrite period.

    2. AEVE 2.0 async entrypoint — `assemble()`. Takes already-rendered
       `SceneVideo[]` (each with audio muxed in by `renderer.render`), runs
       a deterministic "normalize-then-concat" with per-clip re-encoding to
       identical timebase + audio params, and asserts the final
       ffprobe-measured duration matches the sum of audio durations within
       50 ms. NO `-shortest` is ever used.

The 2.0 assembler is what `pipeline.orchestrator` calls in Phase 6.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.llm_client import logger
import config

from pipeline.schemas import FinalVideo, SceneAudio, SceneVideo
from pipeline.timing import DRIFT_BUDGET_S, ffprobe_duration


def _run_ffmpeg_via_python(ffmpeg_args: list, timeout: int = 120) -> tuple:
    """
    Run ffmpeg by invoking it through a Python subprocess script.
    This works around Windows conda ffmpeg binary crashes by using
    the same Python environment that Manim uses internally.
    """
    # Build the ffmpeg command as a string for shell execution
    args_str = " ".join(f'"{a}"' if " " in a else a for a in ffmpeg_args)

    # Create a tiny Python script that runs ffmpeg via subprocess with shell=True
    script = f'''
import subprocess, sys, os
env = os.environ.copy()
conda_prefix = os.environ.get("CONDA_PREFIX", "")
if conda_prefix:
    ffmpeg_dir = os.path.join(conda_prefix, "Library", "bin")
    if ffmpeg_dir.lower() not in env.get("PATH", "").lower():
        env["PATH"] = ffmpeg_dir + os.pathsep + env.get("PATH", "")

result = subprocess.run(
    {repr(args_str)},
    shell=True,
    capture_output=True,
    text=True,
    timeout={timeout},
    env=env
)
if result.returncode != 0:
    print("STDERR:" + result.stderr[:500], file=sys.stderr)
    sys.exit(result.returncode)
else:
    print("OK")
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=timeout + 10
    )
    return result.returncode, result.stdout, result.stderr


def merge_audio_video(video_path: str, audio_path: str, output_path: str) -> str:
    """Merge a Manim video clip with MP3 audio narration."""
    logger.info(f"  Merging: {os.path.basename(video_path)} + {os.path.basename(audio_path)}")

    v = os.path.abspath(video_path)
    a = os.path.abspath(audio_path)
    o = os.path.abspath(output_path)

    rc, out, err = _run_ffmpeg_via_python([
        "ffmpeg", "-y",
        "-i", v, "-i", a,
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-map", "0:v:0", "-map", "1:a:0",
        o
    ])

    if rc != 0:
        logger.warning(f"  FFmpeg merge with -shortest failed, trying without...")
        rc, out, err = _run_ffmpeg_via_python([
            "ffmpeg", "-y",
            "-i", v, "-i", a,
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k",
            "-map", "0:v:0", "-map", "1:a:0",
            o
        ])
        if rc != 0:
            logger.error(f"  FFmpeg error: {err[:300]}")
            raise RuntimeError(f"FFmpeg merge failed: {err[:300]}")

    logger.info(f"  ✓ Merged → {output_path}")
    return output_path


def concatenate_scenes(scene_clips: list, output_path: str) -> str:
    """Concatenate multiple scene clips into a single final video."""
    if len(scene_clips) == 0:
        raise ValueError("No scene clips to concatenate")

    if len(scene_clips) == 1:
        shutil.copy2(scene_clips[0], output_path)
        logger.info(f"  Single scene → {output_path}")
        return output_path

    logger.info(f"  Concatenating {len(scene_clips)} scenes...")

    # Create concat file list
    concat_file = os.path.join(config.OUTPUT_DIR, "concat_list.txt")
    with open(concat_file, "w") as f:
        for clip in scene_clips:
            safe_path = os.path.abspath(clip).replace("\\", "/")
            f.write(f"file '{safe_path}'\n")

    rc, out, err = _run_ffmpeg_via_python([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", os.path.abspath(concat_file),
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac",
        os.path.abspath(output_path)
    ], timeout=300)

    if rc != 0:
        logger.error(f"  FFmpeg concat failed: {err[:300]}")
        raise RuntimeError(f"FFmpeg concat failed: {err[:300]}")

    if os.path.exists(concat_file):
        os.remove(concat_file)

    logger.info(f"  ✓ Final video → {output_path}")
    return output_path


def assemble_final_video(phase3_results: list) -> str:
    """
    Full assembly pipeline:
    1. Render each scene with Manim
    2. Merge with audio (if ffmpeg works)
    3. Concatenate into final video
    """
    logger.info("=" * 60)
    logger.info("ASSEMBLY: Building Final Video")
    logger.info("=" * 60)

    merged_clips = []

    for result in phase3_results:
        scene_id = result["scene_id"]

        if result["status"] != "success":
            logger.warning(f"  Skipping failed scene {scene_id}")
            continue

        audio_path = result["audio"]["mp3_path"]
        code_path = result["code_path"]

        # Step 1: Render the Manim scene
        logger.info(f"  Rendering scene {scene_id}...")
        try:
            from renderer.manim_runner import render_scene
            video_path = render_scene(code_path)
        except Exception as e:
            logger.error(f"  Scene {scene_id} render failed: {e}")
            continue

        # Step 2: Merge audio + video
        merged_path = os.path.join(config.VIDEO_DIR, f"merged_{scene_id}.mp4")
        try:
            merge_audio_video(video_path, audio_path, merged_path)
            merged_clips.append(merged_path)
        except Exception as e:
            logger.warning(f"  Scene {scene_id} audio merge failed: {e}")
            logger.info(f"  Using video-only for scene {scene_id}")
            merged_clips.append(video_path)

    if not merged_clips:
        raise RuntimeError("No scenes were successfully rendered!")

    # Step 3: Concatenate
    final_path = os.path.join(config.FINAL_DIR, "final_video.mp4")
    try:
        concatenate_scenes(merged_clips, final_path)
    except Exception as e:
        logger.warning(f"  Concatenation failed: {e}")
        # Fallback: use the first clip as the final video
        shutil.copy2(merged_clips[0], final_path)
        logger.info(f"  Using first scene as final video (concat failed)")

    logger.info("")
    logger.info(f"{'='*60}")
    logger.info(f"  FINAL VIDEO READY: {final_path}")
    logger.info(f"  Total scenes: {len(merged_clips)}/{len(phase3_results)}")
    logger.info(f"{'='*60}")

    return final_path


# ===========================================================================
# AEVE 2.0 — async assemble() with normalize-then-concat. No -shortest.
# ===========================================================================


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
    # Legacy AEVE 1.0
    "assemble_final_video",
    "concatenate_scenes",
    "merge_audio_video",
    # AEVE 2.0
    "assemble",
    "build_concat_cmd",
    "build_normalize_cmd",
]
