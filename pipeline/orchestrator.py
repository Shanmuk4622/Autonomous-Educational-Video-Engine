"""
AEVE 2.0 — top-level pipeline orchestrator.

Threads the AEVE 2.0 phases together:

    Phase 0  StyleManifest        deterministic Python (no LLM)
    Phase 1  Solver       (S)     math reasoning      -> DeepSolution
    Phase 2  Director     (D)     scene plan          -> Storyboard
    Phase 3  Narrator+TTS (N+E)   spoken + timeline   -> SceneAudio[]   (per scene)
    Phase 4  Animator     (A)     Manim code          -> SceneCode[]    (per scene)
    Phase 5  Render+Healer (R+H)  Manim render + heal -> SceneVideo[]   (per scene)
    Phase 6  Assembler            normalize + concat  -> FinalVideo

Day 5 scope: full pipeline 0 → 6. `run_pipeline()` returns a `PipelineResult`
with `final_video: FinalVideo`. Heavy CPU lives behind two semaphores:
LLM_SEM caps Narrator/Animator/Healer concurrency; RENDER_SEM caps the
manim+ffmpeg subprocess fan-out.

Concurrency model:
    LLM_SEM    = asyncio.Semaphore(4)   # caps narrator + animator concurrency
    RENDER_SEM = asyncio.Semaphore(2)   # reserved for Day 4 (manim+ffmpeg)

Within a scene the per-scene flow is forced serial: Narrator must finish
before TTS starts (TTS needs the polished text), and TTS must finish before
the Animator runs (Animator needs the measured audio duration).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import config

from pipeline.animator import animate
from pipeline.carryover import empty_carry
from pipeline.director import direct
from pipeline.narrator import polish
from pipeline.schemas import (
    DeepSolution,
    FinalVideo,
    SceneAudio,
    SceneCarry,
    SceneCode,
    SceneVideo,
    Storyboard,
    StoryboardScene,
    StyleManifest,
)
from pipeline.solver import solve
from pipeline.style import build_style_manifest, write_style_artifacts
from pipeline.tts import synthesize
from renderer.assembler import assemble
from renderer.render import RenderConfig, render_scene

logger = logging.getLogger("AEVE")

LLM_SEM = asyncio.Semaphore(4)
RENDER_SEM = asyncio.Semaphore(2)


@dataclass
class PipelineResult:
    """Return type of run_pipeline() — Day 5 surface.

    All six AEVE 2.0 phases populated. `final_video` is the `.mp4` shipped
    to disk; everything else is the per-scene record useful for the SSE
    progress feed and the CI sync gate.
    """

    style: StyleManifest
    solution: DeepSolution
    storyboard: Storyboard
    scene_audios: list[SceneAudio]
    scene_codes: list[SceneCode]
    scene_videos: list[SceneVideo]
    final_video: FinalVideo


# ---------------------------------------------------------------------------
# Per-scene flow (Phase 3)
# ---------------------------------------------------------------------------


def _audio_path(audio_dir: Path, scene_id: str) -> Path:
    return audio_dir / f"scene_{scene_id}.mp3"


def _timeline_path(audio_dir: Path, scene_id: str) -> Path:
    return audio_dir / f"scene_{scene_id}.timeline.json"


async def _narrate_and_synthesize(
    scene: StoryboardScene, audio_dir: Path
) -> SceneAudio:
    """Polish narration → edge-tts synthesize → ffprobe duration. Serial."""
    async with LLM_SEM:
        narration = await polish(scene)
    audio = await synthesize(
        text=narration,
        out_path=_audio_path(audio_dir, scene.scene_id),
        scene_id=scene.scene_id,
    )
    timeline_path = _timeline_path(audio_dir, scene.scene_id)
    timeline_path.write_text(
        json.dumps(
            {
                "scene_id": audio.scene_id,
                "duration_s": audio.duration_s,
                "narration_final": audio.narration_final,
                "word_timeline": [w.model_dump() for w in audio.word_timeline],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    logger.info(
        "[orchestrator] scene %s ready — %.3fs, %d words, timeline=%s",
        scene.scene_id,
        audio.duration_s,
        len(audio.word_timeline),
        timeline_path.name,
    )
    return audio


async def _phase3_fanout(
    storyboard: Storyboard, audio_dir: Path
) -> list[SceneAudio]:
    """Fan out Narrator+TTS across scenes; return SceneAudio in scene-id order."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        asyncio.create_task(_narrate_and_synthesize(scene, audio_dir))
        for scene in storyboard.scenes
    ]
    audios = await asyncio.gather(*tasks)
    audios.sort(key=lambda a: a.scene_id)  # belt-and-suspenders; gather preserves order
    return list(audios)


# ---------------------------------------------------------------------------
# Per-scene flow (Phase 4)
# ---------------------------------------------------------------------------


async def _animate_one(
    scene: StoryboardScene,
    audio: SceneAudio,
    prior_carry: SceneCarry,
    style: StyleManifest,
    scenes_dir: Path,
) -> SceneCode:
    async with LLM_SEM:
        return await animate(
            scene=scene,
            audio=audio,
            prior_carry=prior_carry,
            style=style,
            scenes_dir=scenes_dir,
        )


async def _phase5_render_one(
    code: SceneCode,
    audio: SceneAudio,
    style: StyleManifest,
    video_dir: Path,
) -> SceneVideo:
    async with RENDER_SEM:
        return await render_scene(
            code=code,
            audio=audio,
            video_dir=video_dir,
            style=style,
            cfg=RenderConfig(),
        )


async def _phase5_fanout(
    codes: list[SceneCode],
    audios: list[SceneAudio],
    style: StyleManifest,
    video_dir: Path,
) -> list[SceneVideo]:
    """Render scenes in parallel, capped by RENDER_SEM. Returns
    SceneVideo[] in scene-id order. Healer-aided retries are inside
    `render_scene`."""
    video_dir.mkdir(parents=True, exist_ok=True)
    audios_by_id = {a.scene_id: a for a in audios}
    tasks = [
        asyncio.create_task(
            _phase5_render_one(c, audios_by_id[c.scene_id], style, video_dir)
        )
        for c in codes
    ]
    videos = await asyncio.gather(*tasks)
    videos.sort(key=lambda v: v.scene_id)
    return list(videos)


async def _phase4_fanout(
    storyboard: Storyboard,
    audios: list[SceneAudio],
    style: StyleManifest,
    scenes_dir: Path,
) -> list[SceneCode]:
    """Fan out the Animator across scenes; return SceneCode in scene-id order.

    Carryover note: at Day 4 there are no real carry.json files yet (those
    are written at render time by the rendered Manim scene). Each animator
    invocation therefore receives `empty_carry(scene_id)`. Day 5+ will read
    the prior scene's carry from disk after its render completes and chain
    them together — at which point this fan-out becomes a sequential walk
    rather than a gather.
    """
    scenes_dir.mkdir(parents=True, exist_ok=True)
    audios_by_id = {a.scene_id: a for a in audios}
    tasks = [
        asyncio.create_task(
            _animate_one(
                scene=scene,
                audio=audios_by_id[scene.scene_id],
                prior_carry=empty_carry(scene.scene_id),
                style=style,
                scenes_dir=scenes_dir,
            )
        )
        for scene in storyboard.scenes
    ]
    codes = await asyncio.gather(*tasks)
    codes.sort(key=lambda c: c.scene_id)
    return list(codes)


# ---------------------------------------------------------------------------
# Top-level entrypoint
# ---------------------------------------------------------------------------


async def run_pipeline(
    query: str,
    *,
    target_seconds: int = 60,
    image_hint: str | None = None,
    output_dir: Path | None = None,
) -> PipelineResult:
    """Run AEVE 2.0 phases 0-3 end-to-end.

    Args:
        query: Free-text user prompt.
        target_seconds: Desired total runtime (clamped to [20, 180]).
        image_hint: Optional caption/description for an uploaded image.
        output_dir: Where artifacts land. Defaults to `config.OUTPUT_DIR`.

    Returns:
        `PipelineResult` with style, solution, storyboard, and per-scene audio.

    Raises:
        LLMError: solver or director chain exhausted.
        RuntimeError: edge-tts voice chain exhausted for a scene.
    """
    out = Path(output_dir) if output_dir else Path(config.OUTPUT_DIR)
    audio_dir = out / "audio"
    scenes_dir = out / "scenes"
    video_dir = out / "video"
    final_dir = out / "final"
    out.mkdir(parents=True, exist_ok=True)

    # Phase 0 — deterministic style
    logger.info("[orchestrator] phase 0 — building StyleManifest")
    style = build_style_manifest()
    write_style_artifacts(style, out)

    # Phase 1 — Solver
    logger.info("[orchestrator] phase 1 — solver")
    solution = await solve(query, image_hint=image_hint)

    # Phase 2 — Director
    logger.info("[orchestrator] phase 2 — director")
    storyboard = await direct(
        solution, target_seconds=target_seconds, style=style
    )

    # Phase 3 — Narrator + TTS fan-out
    logger.info(
        "[orchestrator] phase 3 — fan out narrator+tts across %d scenes",
        len(storyboard.scenes),
    )
    scene_audios = await _phase3_fanout(storyboard, audio_dir)

    # Phase 4 — Animator fan-out (each scene gated by AST predictor)
    logger.info(
        "[orchestrator] phase 4 — fan out animator across %d scenes",
        len(storyboard.scenes),
    )
    scene_codes = await _phase4_fanout(storyboard, scene_audios, style, scenes_dir)

    # Phase 5 — Render + heal (subprocess-heavy; capped by RENDER_SEM)
    logger.info(
        "[orchestrator] phase 5 — render+heal across %d scenes",
        len(scene_codes),
    )
    scene_videos = await _phase5_fanout(scene_codes, scene_audios, style, video_dir)

    # Phase 6 — Assemble (single ffmpeg pass)
    logger.info("[orchestrator] phase 6 — assemble final video")
    final_video = await assemble(
        scene_videos=scene_videos,
        scene_audios=scene_audios,
        final_dir=final_dir,
    )

    total_audio = sum(a.duration_s for a in scene_audios)
    logger.info(
        "[orchestrator] phases 0-6 complete: %d scenes, audio=%.2fs, "
        "final=%.2fs (drift=%dms) -> %s",
        len(scene_codes),
        total_audio,
        final_video.total_duration_s,
        final_video.total_drift_ms,
        final_video.mp4_path,
    )
    return PipelineResult(
        style=style,
        solution=solution,
        storyboard=storyboard,
        scene_audios=scene_audios,
        scene_codes=scene_codes,
        scene_videos=scene_videos,
        final_video=final_video,
    )


# ---------------------------------------------------------------------------
# CLI smoke harness — `python -m pipeline.orchestrator "<query>"`
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="pipeline.orchestrator",
        description="AEVE 2.0 — run phases 0-3 end-to-end (Day 3 smoke harness).",
    )
    parser.add_argument("query", help="User prompt, e.g. 'Prove the Pythagorean theorem.'")
    parser.add_argument(
        "--target-seconds",
        type=int,
        default=60,
        help="Desired total runtime in seconds (default: 60).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: config.OUTPUT_DIR).",
    )
    args = parser.parse_args()

    _configure_logging()
    result = asyncio.run(
        run_pipeline(
            args.query,
            target_seconds=args.target_seconds,
            output_dir=args.output_dir,
        )
    )

    print(f"\n=== AEVE 2.0 phases 0-6 complete ===")
    print(f"topic:       {result.solution.topic}")
    print(f"difficulty:  {result.solution.difficulty}")
    print(f"scenes:      {len(result.storyboard.scenes)}")
    print(f"target:      {result.storyboard.total_target_seconds}s")
    print(f"audio total: {sum(a.duration_s for a in result.scene_audios):.2f}s")
    print(f"final mp4:   {result.final_video.mp4_path}")
    print(
        f"final dur:   {result.final_video.total_duration_s:.2f}s "
        f"(drift {result.final_video.total_drift_ms:+d}ms)"
    )
    codes_by_id = {c.scene_id: c for c in result.scene_codes}
    videos_by_id = {v.scene_id: v for v in result.scene_videos}
    for a in result.scene_audios:
        c = codes_by_id.get(a.scene_id)
        v = videos_by_id.get(a.scene_id)
        line = (
            f"  scene {a.scene_id}: audio={a.duration_s:.2f}s"
            + (f" predicted={c.predicted_runtime_s:.2f}s" if c else "")
            + (
                f" rendered={v.measured_duration_s:.2f}s "
                f"(drift {v.drift_ms:+d}ms"
                + (f", healer x{v.healer_attempts}" if v.used_healer else "")
                + ")"
                if v
                else ""
            )
        )
        print(line)


if __name__ == "__main__":
    _main()


__all__ = ["LLM_SEM", "RENDER_SEM", "PipelineResult", "run_pipeline"]
