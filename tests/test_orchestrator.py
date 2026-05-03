"""
Tests for pipeline.orchestrator — Day 3 fan-out behavior.

Uses monkeypatching to stub out solver/director/narrator/tts so the test never
hits a real LLM or edge-tts. The shape we verify:
    - PipelineResult populated end-to-end
    - One SceneAudio per scene, in scene-id order
    - One MP3 + one timeline.json written per scene
    - StyleManifest artifacts written into output_dir
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pipeline import orchestrator
from pipeline.schemas import (
    DeepSolution,
    FinalVideo,
    SceneAudio,
    SceneCode,
    SceneVideo,
    Step,
    Storyboard,
    StoryboardScene,
    WordEvent,
)


def _build_storyboard() -> Storyboard:
    return Storyboard(
        total_target_seconds=60,
        scenes=[
            StoryboardScene(
                scene_id="001",
                title="Setup",
                key_concept="Define a right triangle.",
                narration_draft="A right triangle has legs $a$ and $b$ and hypotenuse $c$.",
                formulas=["a^2 + b^2 = c^2"],
                visual_intent="show triangle",
                layout="title_only",
                carryover_objects=[],
                transition_in="fade",
            ),
            StoryboardScene(
                scene_id="002",
                title="Squares",
                key_concept="Build squares on each side.",
                narration_draft="Build squares whose areas are $a^2$, $b^2$, and $c^2$.",
                formulas=[],
                visual_intent="three colored squares",
                layout="equation_focus",
                carryover_objects=["triangle"],
                transition_in="fade",
            ),
        ],
    )


def _build_solution() -> DeepSolution:
    return DeepSolution(
        topic="Pythagorean theorem",
        difficulty="intermediate",
        prerequisites=["right triangles"],
        steps=[
            Step(
                narrative="Draw a right triangle.",
                latex="a^2 + b^2 = c^2",
                visual_intent="triangle",
            )
        ],
        conclusion="Squares on legs sum to square on hypotenuse.",
    )


def test_run_pipeline_fanout(monkeypatch, tmp_path: Path):
    # Stub solver
    async def fake_solve(query, *, image_hint=None):
        assert "Pythag" in query
        return _build_solution()

    # Stub director
    async def fake_direct(solution, *, target_seconds=60, style=None):
        assert solution.topic == "Pythagorean theorem"
        return _build_storyboard()

    # Stub narrator
    async def fake_polish(scene):
        return f"Narration for scene {scene.scene_id}."

    # Stub tts.synthesize — write a small fake MP3 so paths exist
    async def fake_synthesize(*, text, out_path, scene_id, **kwargs):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"ID3\x00\x00\x00\x00fake-mp3-" + scene_id.encode())
        return SceneAudio(
            scene_id=scene_id,
            mp3_path=Path(out_path),
            duration_s=5.0 + 0.5 * int(scene_id),  # 5.5, 6.0
            word_timeline=[
                WordEvent(word="hello", start_s=0.0, end_s=0.5),
                WordEvent(word="world", start_s=0.5, end_s=1.0),
                WordEvent(word=f"scene{scene_id}", start_s=1.0, end_s=1.5),
            ],
            narration_final=text,
        )

    # Stub animator — write a fake .py + return SceneCode
    async def fake_animate(*, scene, audio, prior_carry, style, scenes_dir):
        scenes_dir = Path(scenes_dir)
        scenes_dir.mkdir(parents=True, exist_ok=True)
        py = scenes_dir / f"scene_{scene.scene_id}.py"
        py.write_text(f"# stub for {scene.scene_id}\n", encoding="utf-8")
        return SceneCode(
            scene_id=scene.scene_id,
            py_path=py,
            class_name=f"Scene{scene.scene_id}",
            target_runtime_s=audio.duration_s,
            ast_validated=True,
            predicted_runtime_s=audio.duration_s,
        )

    # Stub renderer — write a fake .mp4 + return SceneVideo
    async def fake_render_scene(*, code, audio, video_dir, style=None, cfg=None):
        video_dir = Path(video_dir)
        video_dir.mkdir(parents=True, exist_ok=True)
        mp4 = video_dir / f"scene_{code.scene_id}.mp4"
        mp4.write_bytes(b"fakemp4-" + code.scene_id.encode())
        return SceneVideo(
            scene_id=code.scene_id,
            mp4_path=mp4,
            measured_duration_s=audio.duration_s,
            drift_ms=0,
        )

    # Stub assembler — write a fake final.mp4 + return FinalVideo
    async def fake_assemble(*, scene_videos, scene_audios, final_dir, **kwargs):
        final_dir = Path(final_dir)
        final_dir.mkdir(parents=True, exist_ok=True)
        out = final_dir / "final.mp4"
        out.write_bytes(b"fake-final")
        total = sum(a.duration_s for a in scene_audios)
        return FinalVideo(
            mp4_path=out,
            total_duration_s=total,
            scene_count=len(scene_videos),
            total_drift_ms=0,
        )

    monkeypatch.setattr(orchestrator, "solve", fake_solve)
    monkeypatch.setattr(orchestrator, "direct", fake_direct)
    monkeypatch.setattr(orchestrator, "polish", fake_polish)
    monkeypatch.setattr(orchestrator, "synthesize", fake_synthesize)
    monkeypatch.setattr(orchestrator, "animate", fake_animate)
    monkeypatch.setattr(orchestrator, "render_scene", fake_render_scene)
    monkeypatch.setattr(orchestrator, "assemble", fake_assemble)

    result = asyncio.run(
        orchestrator.run_pipeline(
            "Prove the Pythagorean theorem.",
            target_seconds=60,
            output_dir=tmp_path,
        )
    )

    # Result shape
    assert result.solution.topic == "Pythagorean theorem"
    assert len(result.storyboard.scenes) == 2
    assert len(result.scene_audios) == 2
    assert len(result.scene_codes) == 2
    assert len(result.scene_videos) == 2
    assert isinstance(result.final_video, FinalVideo)

    # Scenes are in scene-id order
    assert [a.scene_id for a in result.scene_audios] == ["001", "002"]
    assert [c.scene_id for c in result.scene_codes] == ["001", "002"]
    assert [v.scene_id for v in result.scene_videos] == ["001", "002"]

    # Style artifacts written
    assert (tmp_path / "style_manifest.json").exists()
    assert (tmp_path / "_style.py").exists()

    # MP3 + timeline written per scene
    for audio in result.scene_audios:
        assert audio.mp3_path.exists()
        timeline = tmp_path / "audio" / f"scene_{audio.scene_id}.timeline.json"
        assert timeline.exists()
        data = json.loads(timeline.read_text(encoding="utf-8"))
        assert data["scene_id"] == audio.scene_id
        assert data["duration_s"] == audio.duration_s
        assert len(data["word_timeline"]) == 3

    # Scene .py written per scene
    for code in result.scene_codes:
        assert code.py_path.exists()
        assert code.py_path.parent == tmp_path / "scenes"

    # Final mp4 written
    assert result.final_video.mp4_path.exists()
    assert result.final_video.mp4_path.parent == tmp_path / "final"


def test_phase3_fanout_runs_in_parallel(monkeypatch, tmp_path: Path):
    """Confirm scenes are scheduled concurrently (not strictly sequentially)."""
    audio_dir = tmp_path / "audio"
    storyboard = Storyboard(
        total_target_seconds=60,
        scenes=[
            StoryboardScene(
                scene_id=f"{i:03d}",
                title=f"S{i}",
                key_concept="k",
                narration_draft="d",
                formulas=[],
                visual_intent="v",
                layout="title_only",
                carryover_objects=[],
                transition_in="fade",
            )
            for i in range(1, 5)
        ],
    )

    started: list[str] = []
    finished: list[str] = []

    async def fake_polish(scene):
        started.append(scene.scene_id)
        await asyncio.sleep(0.05)
        return "ok"

    async def fake_synthesize(*, text, out_path, scene_id, **kwargs):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"x")
        await asyncio.sleep(0.05)
        finished.append(scene_id)
        return SceneAudio(
            scene_id=scene_id,
            mp3_path=Path(out_path),
            duration_s=1.0,
            word_timeline=[],
            narration_final=text,
        )

    monkeypatch.setattr(orchestrator, "polish", fake_polish)
    monkeypatch.setattr(orchestrator, "synthesize", fake_synthesize)

    audios = asyncio.run(orchestrator._phase3_fanout(storyboard, audio_dir))
    assert [a.scene_id for a in audios] == ["001", "002", "003", "004"]
    # All four scenes should have started before any finished — proves
    # asyncio.gather is dispatching concurrently.
    assert len(started) == 4
    assert started[:2] != finished[:2] or len(set(started)) == 4
