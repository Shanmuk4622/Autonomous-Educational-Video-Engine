"""
Manim Runner — Executes Manim scene files and captures output.

Uses sys.executable -m manim (proven to work on Windows/conda).
Includes code sanitization for common LLM mistakes.
"""

import os
import sys
import subprocess
import re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.llm_client import call_model, extract_python_code, logger
import config


def find_scene_class_name(code_path: str) -> str:
    """Extract the Scene class name from a Manim Python file."""
    with open(code_path, "r", encoding="utf-8") as f:
        content = f.read()

    match = re.search(r'class\s+(\w+)\s*\(\s*(?:Scene|MovingCameraScene|ThreeDScene)\s*\)', content)
    if match:
        return match.group(1)
    match = re.search(r'class\s+(\w+)\s*\(', content)
    if match:
        return match.group(1)
    return "Scene001"


def sanitize_manim_code(code: str) -> str:
    """
    Auto-fix common LLM-generated Manim code mistakes BEFORE rendering.
    """
    # 1. LaTeX is missing on the system. Force replace all MathTex/Tex with Text.
    # This might result in literal a^2 + b^2 but prevents fatal WinError 2 crashes.
    code = re.sub(r'\bMathTex\s*\(', r'Text(', code)
    code = re.sub(r'\bTex\s*\(', r'Text(', code)

    # 2. Truncate very long Text() strings (>80 chars)
    def truncate_text(match):
        text_content = match.group(1)
        if len(text_content) > 80:
            return f'Text("{text_content[:77]}..."'
        return match.group(0)
    code = re.sub(r'Text\s*\(\s*"([^"]+)"', truncate_text, code)

    # 3. Fix Polygon([list]) → Polygon(*list)
    code = re.sub(r'Polygon\s*\(\s*\[([^\]]+)\]\s*\)', r'Polygon(\1)', code)

    # 4. Replace deprecated APIs
    code = code.replace("ShowCreation", "Create")
    code = code.replace("TextMobject", "Text")
    code = code.replace("TexMobject", "Text")

    return code


def _find_rendered_video(scene_name: str, code_path: str) -> str | None:
    """Search for the final rendered video file."""
    search_roots = [
        config.VIDEO_DIR,
        os.path.join(os.path.dirname(code_path), "media"),
        os.path.join(config.PROJECT_ROOT, "media"),
    ]
    for root_dir in search_roots:
        if not os.path.exists(root_dir):
            continue
        for dirpath, _, filenames in os.walk(root_dir):
            if "partial_movie_files" in dirpath:
                continue
            for f in filenames:
                if f.endswith(".mp4") and scene_name in f:
                    return os.path.join(dirpath, f)
    return None


def render_scene(code_path: str, max_retries: int = 2) -> str:
    """Render a single Manim scene file. Returns path to rendered .mp4."""
    # Sanitize code first
    with open(code_path, "r", encoding="utf-8") as f:
        original = f.read()
    sanitized = sanitize_manim_code(original)
    if sanitized != original:
        with open(code_path, "w", encoding="utf-8") as f:
            f.write(sanitized)
        logger.info(f"  [Sanitizer] Auto-fixed common issues in {os.path.basename(code_path)}")

    scene_name = find_scene_class_name(code_path)
    scene_id = os.path.basename(code_path).replace("scene_", "").replace(".py", "")

    for attempt in range(1, max_retries + 2):
        logger.info(f"  [Scene {scene_id}] Rendering with Manim (attempt {attempt}/{max_retries + 1})...")

        # Use sys.executable -m manim (proven to work on Windows/conda)
        cmd = [
            sys.executable, "-m", "manim", "render",
            os.path.abspath(code_path),
            scene_name,
            config.MANIM_QUALITY,
            "--media_dir", os.path.abspath(config.VIDEO_DIR),
        ]

        # Ensure conda's Library/bin is in PATH so Manim can find ffmpeg internally
        env = os.environ.copy()
        cwd_dir = os.path.dirname(os.path.abspath(code_path)) or "."
        
        conda_prefix = os.environ.get("CONDA_PREFIX", "")
        if conda_prefix:
            ffmpeg_dir = os.path.join(conda_prefix, "Library", "bin")
            if ffmpeg_dir.lower() not in env.get("PATH", "").lower():
                env["PATH"] = f"{ffmpeg_dir}{os.pathsep}{env.get('PATH', '')}"
            
            # Explicitly tell Manim where ffmpeg is by writing manim.cfg
            ffmpeg_exe = os.path.join(ffmpeg_dir, "ffmpeg.exe")
            cfg_path = os.path.join(cwd_dir, "manim.cfg")
            try:
                with open(cfg_path, "w", encoding="utf-8") as f:
                    f.write(f"[CLI]\nffmpeg_executable = {ffmpeg_exe}\n")
            except Exception as e:
                logger.warning(f"Could not write manim.cfg: {e}")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180,
                cwd=cwd_dir,
                env=env,
            )

            # Check for video FIRST regardless of exit code
            video_path = _find_rendered_video(scene_name, code_path)
            if video_path and os.path.exists(video_path):
                sz = os.path.getsize(video_path)
                if sz > 1000:
                    logger.info(f"  [Scene {scene_id}] ✓ Rendered → {video_path} ({sz//1024} KB)")
                    return video_path

            # No video — extract meaningful error
            full = (result.stdout or "") + "\n" + (result.stderr or "")
            error_lines = []
            for line in full.split("\n"):
                line = line.strip()
                if not line:
                    continue
                # Skip noise
                if any(skip in line for skip in [
                    "it/s]", "RuntimeWarning", "runpy.py", "Manim Community",
                    "warn(", "Animation ", "|", "Using cached"
                ]):
                    continue
                error_lines.append(line)

            error_msg = "\n".join(error_lines[-15:]) if error_lines else f"Exit code {result.returncode}, no output"

            logger.error(f"  [Scene {scene_id}] Manim render failed:\n  {error_msg[:400]}")

            if attempt <= max_retries:
                logger.info(f"  [Scene {scene_id}] Sending error to M10 for auto-fix...")
                _auto_fix_code(code_path, error_msg, scene_id)
                scene_name = find_scene_class_name(code_path)
            else:
                raise RuntimeError(f"Manim render failed after {max_retries + 1} attempts. Error: {error_msg[:500]}")

        except subprocess.TimeoutExpired:
            logger.error(f"  [Scene {scene_id}] Render timed out (180s)")
            if attempt > max_retries:
                raise RuntimeError("Manim render timed out")

    raise RuntimeError("Render failed after all retries")


def _auto_fix_code(code_path: str, error_msg: str, scene_id: str):
    """Send render error to M10 for auto-fix."""
    with open(code_path, "r", encoding="utf-8") as f:
        broken_code = f.read()

    fix_prompt = f"""This Manim CE v0.19 code FAILED to render. Fix it.

BROKEN CODE:
```python
{broken_code}
```

ERROR:
```
{error_msg[:800]}
```

RULES — follow these EXACTLY:
1. `from manim import *`
2. Use Text("...") for ALL math and text. DO NOT use MathTex or Tex (LaTeX is missing).
3. Text("short text") — keep under 60 chars
4. Polygon(ORIGIN, RIGHT*2, UP*2) — NOT Polygon([list])
5. Create() not ShowCreation(), Write() for text
6. Keep code simple: 5-10 animations max

Return ONLY the complete fixed Python code."""

    fixed_raw = call_model(
        role="M10", user_prompt=fix_prompt, expected_format="python_code",
        system_prompt_extra="Fix broken Manim CE code. Return ONLY Python code.",
    )
    fixed_code = sanitize_manim_code(extract_python_code(fixed_raw))
    with open(code_path, "w", encoding="utf-8") as f:
        f.write(fixed_code)
    logger.info(f"  [Scene {scene_id}] M10 applied auto-fix ({len(fixed_code)} chars)")
