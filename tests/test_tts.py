"""
Unit tests for pipeline.tts.

Live tests (real edge-tts WebSocket call) are gated behind `pytest.mark.live`.
The default suite tests the synthetic-timeline fallback in isolation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.schemas import SceneAudio, WordEvent
from pipeline.tts import VOICE_FALLBACKS, _synthetic_timeline


def test_synthetic_timeline_uniform_split():
    text = "one two three four"
    events = _synthetic_timeline(text, 4.0)
    assert len(events) == 4
    assert all(isinstance(e, WordEvent) for e in events)
    assert events[0].word == "one"
    assert events[3].end_s == pytest.approx(4.0)
    # Even spacing
    durations = [e.end_s - e.start_s for e in events]
    assert all(d == pytest.approx(1.0) for d in durations)


def test_synthetic_timeline_empty_text():
    assert _synthetic_timeline("", 5.0) == []


def test_voice_fallbacks_are_distinct_and_nonempty():
    assert len(VOICE_FALLBACKS) >= 1
    assert len(set(VOICE_FALLBACKS)) == len(VOICE_FALLBACKS)
    for v in VOICE_FALLBACKS:
        assert v.startswith("en-US-")


# ---------------------------------------------------------------------------
# Live test — only with `pytest -m live`. Hits Microsoft's edge-tts endpoint
# and an external ffprobe binary on PATH.
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_tts_live_synthesizes_short_phrase(tmp_path: Path):
    from pipeline.tts import synthesize

    out = tmp_path / "scene_001.mp3"
    audio = await synthesize(
        text="Hello world. This is a synchronization test.",
        out_path=out,
        scene_id="001",
    )
    assert isinstance(audio, SceneAudio)
    assert out.exists() and out.stat().st_size > 0
    assert audio.duration_s > 0.5
    assert len(audio.word_timeline) >= 3
