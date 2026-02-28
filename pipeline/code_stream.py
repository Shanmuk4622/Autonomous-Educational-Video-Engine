"""
Code Stream — M9 (Manim Coder) + M10 (Code Reviewer)

Takes manim_logic and latex_elements from a scene and generates
executable Manim Python code.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.llm_client import call_model, extract_python_code, logger
import config


def generate_manim_code(scene: dict, script_1: str, audio_duration: float) -> str:
    """
    Generate Manim code for a single scene.

    1. M9 (DeepSeek R1) writes the Manim scene class
    2. M10 (Groq Llama 70B) reviews and fixes syntax errors

    Args:
        scene:          Scene dict from manifest
        script_1:       The Deep Solution (for context injection)
        audio_duration: Duration of the narration audio in seconds

    Returns:
        code (str): Executable Manim Python code as a string
    """
    scene_id = scene.get("scene_id", "000")
    manim_logic = scene.get("content", {}).get("manim_logic", "Show the title")
    latex_elements = scene.get("content", {}).get("latex_elements", [])
    voice_over = scene.get("content", {}).get("voice_over", "")

    logger.info(f"  [Scene {scene_id}] M9 (Manim Coder) — Generating code...")

    # ── Step 1: M9 — Code Generation ───────────────────────────
    m9_prompt = f"""Generate a COMPLETE, RUNNABLE Manim Community Edition Python script for this animation scene.

SCENE ID: {scene_id}
ANIMATION INSTRUCTIONS: {manim_logic}
LATEX ELEMENTS TO DISPLAY: {latex_elements}
NARRATION TEXT (for context): {voice_over}
AUDIO DURATION: {audio_duration:.1f} seconds (the animation must last at least this long)

CRITICAL REQUIREMENTS:
1. Start with: `from manim import *`
2. Class name: `Scene{scene_id}(Scene)` with a `construct(self)` method.
3. Use `Text("...")` for ALL text and math (LaTeX is missing, so DO NOT use MathTex/Tex).
4. Use `Text("...")` for plain text (titles, labels).
5. Animations: Write(), FadeIn(), FadeOut(), Create(), Transform(), ReplacementTransform()
6. Position: .to_edge(UP), .shift(DOWN*2), .next_to(obj, RIGHT), .move_to(ORIGIN)
7. End with `self.wait({audio_duration:.1f})` so the scene matches the audio.
8. Add `self.wait(0.5)` pauses between steps.
9. Keep it SIMPLE — 5-10 animation steps max.

ABSOLUTE DO-NOT-USE (these will CRASH):
- ShowCreation → use Create instead
- TextMobject → use Text instead
- TexMobject/MathTex/Tex → use Text instead
- ShowCreationThenDestruction → use Create then FadeOut
- Polygon([list_of_points]) → use Polygon(point1, point2, point3) with *unpacked* args
- add_sound() → audio is separate
- play() with non-Animation args → wrap in FadeIn/Write/Create first

WORKING POLYGON EXAMPLE:
```python
# CORRECT:
triangle = Polygon(ORIGIN, RIGHT*2, UP*2, color=BLUE)
self.play(Create(triangle))

# WRONG (will crash):
triangle = Polygon([ORIGIN, RIGHT*2, UP*2])  # DO NOT pass a list!
```

WORKING EXAMPLE:
```python
from manim import *

class Scene{scene_id}(Scene):
    def construct(self):
        title = Text("Title Here", font_size=40, color=YELLOW)
        self.play(Write(title))
        self.wait(0.5)
        self.play(title.animate.to_edge(UP).scale(0.7))

        eq = Text(r"a^2 + b^2 = c^2", font_size=48)
        self.play(Write(eq))
        self.wait(1)

        self.wait({audio_duration:.1f})
        self.play(*[FadeOut(mob) for mob in self.mobjects])
```

OUTPUT: Return ONLY the Python code. No explanations, no markdown fences — just pure Python starting with `from manim import *`.
"""

    code_raw = call_model(
        role="M9",
        user_prompt=m9_prompt,
        expected_format="python_code",
        context_injection=script_1,
        system_prompt_extra=(
            "You write Manim Community Edition Python code. "
            "You produce COMPLETE, RUNNABLE scripts — no pseudocode, no placeholders. "
            "Use ONLY Manim CE v0.18+ API. "
            "Return ONLY Python code — no markdown blocks, no explanations."
        ),
    )

    code = extract_python_code(code_raw)
    logger.info(f"  [Scene {scene_id}] M9 generated code ({len(code)} chars)")

    # ── Step 2: M10 — Code Review ──────────────────────────────
    logger.info(f"  [Scene {scene_id}] M10 (Code Reviewer) — Reviewing for errors...")

    m10_prompt = f"""Review this Manim CE Python code for syntax errors and API correctness. Fix ALL issues.

```python
{code}
```

CHECK LIST:
1. ✓ Imports: Only `from manim import *` is needed
2. ✓ Class name: Must be `Scene{scene_id}(Scene)` with a `construct(self)` method
3. ✓ Text is used instead of MathTex for all equations.
4. ✓ Animations: Only valid Manim CE methods (Write, FadeIn, FadeOut, Create, Transform, etc.)
5. ✓ No deprecated API: No ShowCreation, TextMobject, TexMobject, ShowCreationThenDestruction
6. ✓ self.play() used for all animations (not just creating objects)
7. ✓ self.wait() at the end lasting {audio_duration:.1f} seconds
8. ✓ No external imports beyond manim
9. ✓ Valid Python syntax (matching parentheses, correct indentation)
10. ✓ Colors are valid Manim constants (WHITE, YELLOW, BLUE, RED, GREEN, etc.)

OUTPUT RULES:
- If the code is PERFECT, return it unchanged.
- If there are issues, return the FIXED code.
- Return ONLY the Python code. No explanations, no markdown, no comments about changes.
"""

    reviewed_raw = call_model(
        role="M10",
        user_prompt=m10_prompt,
        expected_format="python_code",
        system_prompt_extra=(
            "You review and fix Manim CE Python code. "
            "You are an expert at Manim's API. "
            "Return ONLY the corrected Python code — no explanations."
        ),
    )

    reviewed_code = extract_python_code(reviewed_raw)
    logger.info(f"  [Scene {scene_id}] M10 reviewed code ({len(reviewed_code)} chars)")

    # Save to file
    scene_file = os.path.join(config.SCENES_DIR, f"scene_{scene_id}.py")
    with open(scene_file, "w", encoding="utf-8") as f:
        f.write(reviewed_code)
    logger.info(f"  [Scene {scene_id}] Code saved → {scene_file}")

    return reviewed_code
