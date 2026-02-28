"""
Phase III: Distribution Algorithm — The orchestrator.

Takes the Scene Manifest and dispatches parallel audio + code generation
tasks for each scene. Handles per-scene error recovery.
"""

import os
import sys
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.llm_client import logger
from pipeline.audio_stream import run_audio_stream
from pipeline.code_stream import generate_manim_code
from pipeline.sync_engine import sync_scene
import config


def run_phase3(scene_manifest: list, script_1: str) -> list:
    """
    Phase III: Distribution Algorithm

    For each scene in the manifest:
    1. Generate audio (narration + TTS)
    2. Generate Manim code (with script_1 context injection)
    3. Synchronize audio duration with code

    Args:
        scene_manifest: List of scene dicts from Phase II
        script_1:       The Deep Solution for context injection

    Returns:
        results: List of dicts with scene_id, audio info, code path, status
    """
    logger.info("=" * 60)
    logger.info("PHASE III: DISTRIBUTION ALGORITHM")
    logger.info(f"Processing {len(scene_manifest)} scenes...")
    logger.info("=" * 60)

    # Save manifest to disk for recovery
    manifest_path = os.path.join(config.OUTPUT_DIR, "scene_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(scene_manifest, f, indent=2, ensure_ascii=False)
    logger.info(f"Scene manifest saved → {manifest_path}")

    results = []

    for i, scene in enumerate(scene_manifest):
        scene_id = scene.get("scene_id", f"{i+1:03d}")
        logger.info("")
        logger.info(f"─── Scene {scene_id} ({i+1}/{len(scene_manifest)}) ───")

        result = {
            "scene_id": scene_id,
            "status": "pending",
            "audio": None,
            "code_path": None,
            "error": None,
        }

        try:
            # ── Stream 1: Audio ─────────────────────────────────
            logger.info(f"  [Scene {scene_id}] Starting Audio Stream...")
            audio_result = run_audio_stream(scene)
            result["audio"] = audio_result
            audio_duration = audio_result["duration"]
            logger.info(
                f"  [Scene {scene_id}] Audio complete: "
                f"{audio_duration:.1f}s → {audio_result['mp3_path']}"
            )

            # ── Stream 2: Code ──────────────────────────────────
            logger.info(f"  [Scene {scene_id}] Starting Code Stream...")
            code = generate_manim_code(scene, script_1, audio_duration)
            code_path = os.path.join(config.SCENES_DIR, f"scene_{scene_id}.py")
            result["code_path"] = code_path

            # ── Synchronize ─────────────────────────────────────
            logger.info(f"  [Scene {scene_id}] Synchronizing audio-visual timing...")
            sync_scene(code_path, audio_duration)

            result["status"] = "success"
            logger.info(f"  [Scene {scene_id}] ✓ Scene complete!")

        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)
            logger.error(f"  [Scene {scene_id}] ✗ FAILED: {e}")
            logger.error(f"  Continuing with remaining scenes...")

        results.append(result)

    # ── Summary ─────────────────────────────────────────────────
    success = sum(1 for r in results if r["status"] == "success")
    failed = sum(1 for r in results if r["status"] == "failed")
    logger.info("")
    logger.info(f"PHASE III COMPLETE: {success} succeeded, {failed} failed out of {len(results)} scenes.")

    if failed > 0:
        logger.warning(f"Failed scenes:")
        for r in results:
            if r["status"] == "failed":
                logger.warning(f"  Scene {r['scene_id']}: {r['error'][:200]}")

    return results
