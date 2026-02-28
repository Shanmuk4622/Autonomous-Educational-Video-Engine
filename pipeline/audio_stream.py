"""
Audio Stream — M7 (Narration Writer) + M8 (TTS via edge-tts)

Takes voice_over text and produces an MP3 audio file + SRT subtitles.
"""

import os
import sys
import asyncio
import re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.llm_client import call_model, logger
import config


def polish_narration(voice_over: str, scene_id: str) -> str:
    """
    M7 (Narration Writer) — Polish the narration text for natural speech.
    Converts LaTeX inline math to spoken words suitable for TTS.
    """
    logger.info(f"  [Scene {scene_id}] M7 (Narration Writer) — Polishing voiceover...")

    m7_prompt = f"""You are a professional narrator for educational math videos. Convert this narration text into NATURAL SPOKEN ENGLISH that a text-to-speech engine can read aloud.

ORIGINAL NARRATION:
{voice_over}

YOUR TASK:
1. Convert ALL LaTeX math into spoken words:
   - $x^2$ → "x squared"
   - $\\frac{{a}}{{b}}$ → "a over b"
   - $\\sqrt{{x}}$ → "the square root of x"
   - $\\int_0^1 f(x) dx$ → "the integral from zero to one of f of x dx"
   - $\\lim_{{x \\to 0}}$ → "the limit as x approaches zero"
2. Keep the tone EDUCATIONAL and ENGAGING — like a great teacher explaining.
3. Add natural pauses by using commas and periods.
4. Add emphasis words: "Notice that...", "This is important...", "Now, observe..."
5. Keep it concise — aim for 10-20 seconds of speech per scene.
6. Do NOT include any LaTeX or special characters — pure spoken text only.

OUTPUT: Return ONLY the polished narration text, nothing else. No quotes, no labels.
"""

    polished = call_model(
        role="M7",
        user_prompt=m7_prompt,
        expected_format="text",
        system_prompt_extra=(
            "You convert mathematical narration into natural spoken English. "
            "Your output is directly fed to a TTS engine. "
            "Return ONLY the narration text — no labels, no quotes, no formatting."
        ),
    )

    # Clean up: remove any remaining LaTeX or quotes
    polished = polished.strip().strip('"').strip("'")
    polished = re.sub(r'\$[^$]+\$', '', polished)  # Remove any leftover LaTeX

    logger.info(f"  [Scene {scene_id}] M7 polished narration ({len(polished)} chars)")
    return polished


async def _generate_tts_async(text: str, output_mp3: str, output_srt: str) -> float:
    """Internal async TTS generation using edge-tts."""
    import edge_tts

    communicate = edge_tts.Communicate(
        text,
        voice=config.TTS_VOICE,
        rate=config.TTS_RATE,
        volume=config.TTS_VOLUME,
    )

    # Use the simple save() method — most reliable across edge-tts versions
    await communicate.save(output_mp3)

    # Generate subtitles separately
    try:
        submaker = edge_tts.SubMaker()
        communicate2 = edge_tts.Communicate(
            text,
            voice=config.TTS_VOICE,
            rate=config.TTS_RATE,
            volume=config.TTS_VOLUME,
        )
        async for chunk in communicate2.stream():
            if chunk["type"] == "WordBoundary":
                submaker.feed(chunk)
        # Try both API variants for subtitle generation
        srt_content = ""
        if hasattr(submaker, 'get_srt'):
            srt_content = submaker.get_srt()
        elif hasattr(submaker, 'generate_subs'):
            srt_content = submaker.generate_subs()
        if srt_content:
            with open(output_srt, "w", encoding="utf-8") as f:
                f.write(srt_content)
    except Exception as e:
        logger.warning(f"  Subtitle generation failed (non-critical): {e}")

    # Get audio duration
    try:
        from mutagen.mp3 import MP3
        audio = MP3(output_mp3)
        duration = audio.info.length
    except Exception:
        # Fallback: estimate from text length (~150 words per minute)
        word_count = len(text.split())
        duration = (word_count / 150.0) * 60.0

    return duration


def generate_tts(text: str, output_mp3: str, output_srt: str = None) -> float:
    """
    Generate TTS audio from text using edge-tts.

    Args:
        text:       The narration text
        output_mp3: Path for the output MP3 file
        output_srt: Path for the output SRT subtitle file

    Returns:
        duration (float): Audio duration in seconds
    """
    if output_srt is None:
        output_srt = output_mp3.replace(".mp3", ".srt")

    duration = asyncio.run(_generate_tts_async(text, output_mp3, output_srt))
    return duration


def run_audio_stream(scene: dict) -> dict:
    """
    Full audio pipeline for a single scene:
    1. M7 polishes the narration
    2. M8 (edge-tts) generates audio + subtitles

    Args:
        scene: A scene dict from the manifest

    Returns:
        dict with keys: mp3_path, srt_path, duration, narration_text
    """
    scene_id = scene.get("scene_id", "000")
    voice_over = scene.get("content", {}).get("voice_over", "")

    if not voice_over:
        logger.warning(f"  [Scene {scene_id}] No voice_over text found!")
        voice_over = f"Scene {scene_id}"

    # Step 1: Polish narration with M7
    polished_narration = polish_narration(voice_over, scene_id)

    # Step 2: Generate TTS
    mp3_path = os.path.join(config.AUDIO_DIR, f"scene_{scene_id}.mp3")
    srt_path = os.path.join(config.AUDIO_DIR, f"scene_{scene_id}.srt")

    logger.info(f"  [Scene {scene_id}] M8 (TTS) — Generating audio...")
    duration = generate_tts(polished_narration, mp3_path, srt_path)
    logger.info(f"  [Scene {scene_id}] M8 audio generated: {duration:.1f}s → {mp3_path}")

    return {
        "mp3_path": mp3_path,
        "srt_path": srt_path,
        "duration": duration,
        "narration_text": polished_narration,
    }
