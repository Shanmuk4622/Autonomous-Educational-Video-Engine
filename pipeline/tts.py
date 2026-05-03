"""
AEVE 2.0 — Phase 3b: TTS with WordBoundary capture.

edge-tts gives us two things in a single streaming call:

  1. The MP3 byte stream (we write it incrementally to disk).
  2. WordBoundary events with `offset` and `duration` in 100-ns units, which
     we translate into a per-word timeline (`list[WordEvent]`).

That word timeline is the foundation for the Animator's runtime budget — every
visual reveal is anchored to a spoken word, not to a fixed seconds clock.

After streaming completes we run `ffprobe` to obtain the authoritative MP3
duration. The legacy `mutagen` metadata read is gone.

Routing fallback:
    primary    — voice "en-US-AriaNeural"
    fallback 1 — voice "en-US-JennyNeural"
    fallback 2 — voice "en-US-GuyNeural"
On `edge_tts.exceptions.NoAudioReceived` (or any exception during stream) we
walk the voice fallback chain.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Final

import edge_tts

from pipeline.schemas import SceneAudio, WordEvent
from pipeline.timing import ffprobe_duration

logger = logging.getLogger("AEVE")

# 100-nanosecond units to seconds. Aria/Jenny/Guy emit offsets in HNS.
HNS_PER_SECOND: Final[float] = 1.0e7

DEFAULT_VOICE: Final[str] = "en-US-AriaNeural"
VOICE_FALLBACKS: Final[tuple[str, ...]] = (
    "en-US-AriaNeural",
    "en-US-JennyNeural",
    "en-US-GuyNeural",
)
DEFAULT_RATE: Final[str] = "+0%"
DEFAULT_VOLUME: Final[str] = "+0%"


async def _stream_one(
    text: str,
    voice: str,
    out_path: Path,
    *,
    rate: str,
    volume: str,
) -> list[WordEvent]:
    """Stream audio + WordBoundary for a single voice. Returns word events.

    Raises any edge-tts exception unchanged so the caller can fall back.
    """
    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, volume=volume)
    word_events: list[WordEvent] = []
    audio_bytes = bytearray()

    async for chunk in communicate.stream():
        ctype = chunk.get("type")
        if ctype == "audio":
            audio_bytes.extend(chunk["data"])
        elif ctype == "WordBoundary":
            offset_s = float(chunk["offset"]) / HNS_PER_SECOND
            duration_s = float(chunk["duration"]) / HNS_PER_SECOND
            word_events.append(
                WordEvent(
                    word=str(chunk.get("text", "")).strip() or " ",
                    start_s=offset_s,
                    end_s=offset_s + duration_s,
                )
            )

    if not audio_bytes:
        raise RuntimeError(f"edge-tts produced no audio for voice {voice!r}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(out_path.write_bytes, bytes(audio_bytes))
    return word_events


def _synthetic_timeline(text: str, total_s: float) -> list[WordEvent]:
    """If WordBoundary was empty (rare), fabricate a uniform timeline.

    Better than dropping the timeline entirely — gives the Animator something
    coarse to anchor reveals against.
    """
    words = [w for w in text.split() if w]
    if not words:
        return []
    per = total_s / len(words)
    return [
        WordEvent(word=w, start_s=i * per, end_s=(i + 1) * per)
        for i, w in enumerate(words)
    ]


async def synthesize(
    *,
    text: str,
    out_path: Path,
    scene_id: str,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    volume: str = DEFAULT_VOLUME,
) -> SceneAudio:
    """Synthesize narration for a single scene.

    Walks the voice fallback chain on any exception. Always returns a fully
    populated `SceneAudio` (with ffprobe-measured duration_s).

    Args:
        text: Spoken-English narration (already polished by the Narrator).
        out_path: Destination MP3 file.
        scene_id: Zero-padded scene id (e.g. "001").
        voice: Preferred edge-tts voice. Falls back through VOICE_FALLBACKS
            on failure (deduplicated, primary first).
        rate, volume: edge-tts prosody knobs ("+0%", "-10%", etc.).

    Raises:
        RuntimeError: every voice in the fallback chain failed.
    """
    if not text.strip():
        raise ValueError("tts.synthesize: empty text")

    chain: list[str] = []
    seen: set[str] = set()
    for v in (voice, *VOICE_FALLBACKS):
        if v not in seen:
            chain.append(v)
            seen.add(v)

    last_exc: Exception | None = None
    word_events: list[WordEvent] = []
    chosen_voice: str | None = None

    for attempt, v in enumerate(chain, start=1):
        try:
            word_events = await _stream_one(
                text, v, out_path, rate=rate, volume=volume
            )
            chosen_voice = v
            if attempt > 1:
                logger.info(
                    "[tts] scene %s — recovered on voice fallback %d/%d (%s)",
                    scene_id,
                    attempt,
                    len(chain),
                    v,
                )
            break
        except Exception as exc:  # edge-tts raises a variety of types
            last_exc = exc
            logger.warning(
                "[tts] scene %s — voice %s failed: %s", scene_id, v, exc
            )
            continue
    else:
        raise RuntimeError(
            f"all {len(chain)} edge-tts voices failed for scene {scene_id}: {last_exc}"
        )

    duration_s = await ffprobe_duration(out_path)

    if not word_events:
        logger.warning(
            "[tts] scene %s — no WordBoundary events; using synthetic timeline",
            scene_id,
        )
        word_events = _synthetic_timeline(text, duration_s)

    logger.info(
        "[tts] scene %s — voice=%s duration=%.3fs words=%d",
        scene_id,
        chosen_voice,
        duration_s,
        len(word_events),
    )

    return SceneAudio(
        scene_id=scene_id,
        mp3_path=out_path,
        duration_s=duration_s,
        word_timeline=word_events,
        narration_final=text.strip(),
    )


__all__ = [
    "DEFAULT_VOICE",
    "DEFAULT_RATE",
    "DEFAULT_VOLUME",
    "VOICE_FALLBACKS",
    "synthesize",
]
