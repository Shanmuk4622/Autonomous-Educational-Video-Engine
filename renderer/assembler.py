"""
Assembler — Merges Manim video clips with audio narration and concatenates.

Uses Python subprocess via sys.executable to call ffmpeg through a temp script,
working around broken conda ffmpeg binary on Windows.
"""

import os
import sys
import subprocess
import shutil
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.llm_client import logger
import config


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
