"""
End-to-end live test for AEVE 2.0.

Gated behind `@pytest.mark.live` — consumes Groq/OpenRouter quota AND runs
real Manim renders. Run with:

    pytest -m live tests/test_e2e.py

Expected on a 6-core box:
    - 4-7 scenes, 50-70s total
    - <50ms drift between final.mp4 duration and sum(audio.duration_s)
    - wall-clock < 3 min (the rewrite plan target was 2 min)
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from pipeline.schemas import FinalVideo


@pytest.mark.live
def test_pythagoras_e2e(tmp_path: Path):
    """Full pipeline: prompt → final.mp4. Asserts drift + scene count + path."""
    from pipeline.orchestrator import run_pipeline

    start = time.time()
    result = asyncio.run(
        run_pipeline(
            "Prove the Pythagorean theorem with a square-rearrangement proof.",
            target_seconds=60,
            output_dir=tmp_path,
        )
    )
    elapsed = time.time() - start

    # Shape
    assert isinstance(result.final_video, FinalVideo)
    assert result.final_video.mp4_path.exists()
    assert result.final_video.mp4_path.parent == tmp_path / "final"
    assert 4 <= len(result.scene_videos) <= 8

    # Drift gate (the canonical CI assertion from the rewrite plan)
    audio_total = sum(a.duration_s for a in result.scene_audios)
    drift_ms = abs(result.final_video.total_duration_s - audio_total) * 1000
    assert drift_ms < 50, (
        f"final-vs-audio drift {drift_ms:.1f}ms exceeds 50ms budget. "
        f"final={result.final_video.total_duration_s:.3f}s, "
        f"audio_sum={audio_total:.3f}s"
    )

    # Per-scene drift gate
    for v in result.scene_videos:
        assert abs(v.drift_ms) < 50, (
            f"scene {v.scene_id} drift {v.drift_ms:+d}ms exceeds 50ms budget"
        )

    # Wall-clock soft check (informational; not a hard assertion since it
    # depends heavily on host hardware)
    print(
        f"\n[e2e] {len(result.scene_videos)} scenes, "
        f"{audio_total:.1f}s audio, drift {drift_ms:.1f}ms, "
        f"{int(elapsed // 60)}m{int(elapsed % 60)}s wall-clock"
    )


@pytest.mark.live
def test_short_e2e_minimal(tmp_path: Path):
    """A 25s smoke prompt — quickest live check that the chain is wired."""
    from pipeline.orchestrator import run_pipeline

    result = asyncio.run(
        run_pipeline(
            "Explain the formula for the area of a circle.",
            target_seconds=25,
            output_dir=tmp_path,
        )
    )
    assert result.final_video.mp4_path.exists()
    assert result.final_video.scene_count >= 1
