"""
Sync Engine — Manages audio-visual duration synchronization.

Reads MP3 duration and adjusts Manim scene code to match.
"""

import os
import sys
import re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.llm_client import logger


def get_audio_duration(mp3_path: str) -> float:
    """Get duration of an MP3 file in seconds."""
    try:
        from mutagen.mp3 import MP3
        audio = MP3(mp3_path)
        return audio.info.length
    except Exception as e:
        logger.warning(f"  Could not read MP3 duration: {e}. Estimating...")
        # Fallback: estimate from file size (~16KB per second for 128kbps)
        size = os.path.getsize(mp3_path)
        return size / 16000.0


def inject_wait_duration(code: str, duration: float) -> str:
    """
    Ensure the Manim scene code has a final self.wait() matching the audio duration.

    If the code already has a final wait, adjust its value.
    If not, add one before the final FadeOut or at the end of construct().
    """
    # Check if there's already a wait near the end
    # Replace the last self.wait(X) with the correct duration
    lines = code.split("\n")
    last_wait_idx = -1
    for i in range(len(lines) - 1, -1, -1):
        if "self.wait(" in lines[i]:
            last_wait_idx = i
            break

    if last_wait_idx >= 0:
        # Replace the duration in the last wait
        lines[last_wait_idx] = re.sub(
            r'self\.wait\([^)]*\)',
            f'self.wait({duration:.1f})',
            lines[last_wait_idx]
        )
        logger.info(f"  Adjusted final self.wait() to {duration:.1f}s")
    else:
        # Find the end of construct() and add a wait
        for i in range(len(lines) - 1, -1, -1):
            stripped = lines[i].strip()
            if stripped and not stripped.startswith("#"):
                # Add wait before this last line if it's a FadeOut, or after
                indent = "        "
                lines.insert(i + 1, f"{indent}self.wait({duration:.1f})  # Hold for narration")
                logger.info(f"  Inserted self.wait({duration:.1f}) for narration sync")
                break

    return "\n".join(lines)


def sync_scene(scene_code_path: str, audio_duration: float) -> str:
    """
    Synchronize a scene file's duration with its audio.

    Args:
        scene_code_path: Path to the .py scene file
        audio_duration:  Duration of the narration MP3 in seconds

    Returns:
        The updated code (also writes to the file)
    """
    with open(scene_code_path, "r", encoding="utf-8") as f:
        code = f.read()

    updated_code = inject_wait_duration(code, audio_duration)

    with open(scene_code_path, "w", encoding="utf-8") as f:
        f.write(updated_code)

    return updated_code
