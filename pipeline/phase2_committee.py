"""
Phase II: Consensus Committee — M3 (Storyboarder) + M4 (Visual Detailer)
                               + M5 (Technical Critic) + M6 (Finalizer)

Takes Script 1 and produces a structured Scene Manifest (JSON array).
"""

import os
import sys
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.llm_client import call_model, extract_json, logger


def run_phase2(script_1: str) -> list:
    """
    Phase II: Consensus Committee

    1. M3 splits the solution into logical scenes
    2. M4 enriches each scene with Manim animation descriptions
    3. M5 reviews for technical feasibility
    4. M6 produces the final Scene Manifest JSON

    Returns:
        scene_manifest (list): Array of scene objects
    """
    logger.info("=" * 60)
    logger.info("PHASE II: CONSENSUS COMMITTEE")
    logger.info("=" * 60)

    # ── Step 1: M3 — Storyboarder ──────────────────────────────
    logger.info("Step 1/4: M3 (Storyboarder) — Splitting into visual scenes...")

    m3_prompt = f"""You are a storyboard artist for educational math videos. Given the following mathematical solution, split it into VISUAL SCENES for an animated video.

DEEP SOLUTION (Script 1):
{script_1}

YOUR TASK:
1. Break the solution into 3-8 logical scenes. Each scene should cover one key concept or step.
2. For each scene, provide:
   - scene_number: sequential integer
   - title: short descriptive title
   - key_concept: the main idea being taught
   - narration: what the narrator should say (1-3 sentences, natural spoken language)
   - visual_description: what the viewer should see on screen
   - latex_elements: list of LaTeX expressions that appear on screen
   - transition: how this scene connects to the next ("FadeOut", "FadeIn", "Transform")

OUTPUT FORMAT — You MUST return a valid JSON array of scene objects:
```json
[
  {{
    "scene_number": 1,
    "title": "Introduction",
    "key_concept": "...",
    "narration": "...",
    "visual_description": "...",
    "latex_elements": ["$...$"],
    "transition": "FadeOut"
  }}
]
```

RULES:
- Keep narration natural and conversational — this will be read aloud.
- Every LaTeX element must be properly formatted.
- Scenes should flow logically, building understanding step by step.
- Return ONLY the JSON array, no other text.
"""

    storyboard_raw = call_model(
        role="M3",
        user_prompt=m3_prompt,
        expected_format="json_array",
        system_prompt_extra=(
            "You split mathematical solutions into visual scenes for animated videos. "
            "Your output MUST be a valid JSON array. No markdown, no explanations — ONLY JSON."
        ),
    )

    storyboard = extract_json(storyboard_raw)
    logger.info(f"  M3 created {len(storyboard)} scenes")

    # ── Step 2: M4 — Visual Detailer ───────────────────────────
    logger.info("Step 2/4: M4 (Visual Detailer) — Adding Manim animation details...")

    m4_prompt = f"""You are a Manim animation expert. Given this storyboard, add SPECIFIC Manim animation instructions to each scene.

STORYBOARD:
{json.dumps(storyboard, indent=2)}

YOUR TASK — For each scene, ADD a "manim_logic" field describing EXACTLY what Manim should do:
- What objects to create (Text, Circle, Arrow, Axes, NumberPlane, etc.). DO NOT USE MathTex or Tex!
- What animations to use (Write, FadeIn, FadeOut, Transform, Create, ShowPassingFlash, etc.)
- What positioning and colors to use
- Keep animations SIMPLE and achievable — no overly complex 3D or custom shaders

CONSTRAINTS:
- Use ONLY standard Manim Community Edition objects and animations.
- Use Text for all text and equations (LaTeX is missing on the system, do not use MathTex/Tex).
- Axes/NumberPlane for graphs, always specify x_range and y_range.
- Maximum 5-6 animation steps per scene to keep it clean.

OUTPUT FORMAT — Return the SAME JSON array but with "manim_logic" added to each scene:
```json
[
  {{
    "scene_number": 1,
    "title": "...",
    "key_concept": "...",
    "narration": "...",
    "visual_description": "...",
    "latex_elements": ["..."],
    "manim_logic": "Create a Title text 'topic name' at the top. Write the first equation using Text. FadeIn an explanatory Text below.",
    "transition": "FadeOut"
  }}
]
```

Return ONLY the JSON array.
"""

    detailed_raw = call_model(
        role="M4",
        user_prompt=m4_prompt,
        expected_format="json_array",
        system_prompt_extra=(
            "You are a Manim animation expert. You add specific, implementable animation "
            "instructions to scene storyboards. Use only standard Manim CE v0.18+ API. "
            "Output MUST be valid JSON array."
        ),
    )

    detailed_scenes = extract_json(detailed_raw)
    logger.info(f"  M4 enriched {len(detailed_scenes)} scenes with Manim logic")

    # ── Step 3: M5 — Technical Critic ──────────────────────────
    logger.info("Step 3/4: M5 (Technical Critic) — Reviewing feasibility...")

    m5_prompt = f"""You are a Manim technical reviewer. Review this scene manifest for TECHNICAL FEASIBILITY.

SCENE MANIFEST:
{json.dumps(detailed_scenes, indent=2)}

YOUR TASK:
1. For EACH scene, check if the "manim_logic" is actually implementable using Manim Community Edition.
2. Flag any animations that:
   - Use deprecated or non-existent Manim API calls
   - Are too complex to render reliably
   - Have conflicting or overlapping objects
   - Have LaTeX syntax errors
3. SUGGEST concrete fixes for any issues found.

OUTPUT FORMAT — Return a JSON array with "review" and "issues" added:
```json
[
  {{
    "scene_number": 1,
    "title": "...",
    "manim_logic": "...(original or corrected)...",
    "review": "PASS",
    "issues": [],
    "narration": "...",
    "latex_elements": ["..."],
    "transition": "..."
  }}
]
```

Use "review": "PASS" if no issues, or "review": "FIXED" if you corrected something.
Include ALL original fields. Return ONLY JSON.
"""

    reviewed_raw = call_model(
        role="M5",
        user_prompt=m5_prompt,
        expected_format="json_array",
        system_prompt_extra=(
            "You are a technical reviewer for Manim CE code. "
            "You catch infeasible animations and fix them. "
            "You know the Manim CE API inside and out. "
            "Output MUST be valid JSON array."
        ),
    )

    reviewed_scenes = extract_json(reviewed_raw)
    logger.info(f"  M5 reviewed {len(reviewed_scenes)} scenes")
    for s in reviewed_scenes:
        status = s.get("review", "?")
        issues = s.get("issues", [])
        logger.info(f"    Scene {s.get('scene_number', '?')}: {status} ({len(issues)} issues)")

    # ── Step 4: M6 — Finalizer ─────────────────────────────────
    logger.info("Step 4/4: M6 (Finalizer) — Producing final Scene Manifest...")

    m6_prompt = f"""You are the final assembler for an educational video pipeline. Take the reviewed scenes and produce the DEFINITIVE Scene Manifest.

REVIEWED SCENES:
{json.dumps(reviewed_scenes, indent=2)}

YOUR TASK:
1. Clean up any remaining issues
2. Ensure consistent formatting across all scenes
3. Ensure every scene has ALL required fields
4. Add "duration" estimates to sync_parameters (in seconds, based on narration length)
5. Ensure LaTeX is double-escaped for JSON storage (\\\\frac not \\frac)

OUTPUT FORMAT — Return the FINAL Scene Manifest as a JSON array:
```json
[
  {{
    "scene_id": "001",
    "meta_data": {{"topic": "...", "complexity": "Basic|Intermediate|Advanced"}},
    "content": {{
      "voice_over": "Natural narration text with $inline$ LaTeX if needed",
      "manim_logic": "Specific step-by-step Manim animation instructions",
      "latex_elements": ["$formula1$", "$formula2$"]
    }},
    "sync_parameters": {{"duration": "auto", "transition": "FadeOut"}}
  }}
]
```

CRITICAL RULES:
- scene_id must be zero-padded: "001", "002", etc.
- voice_over should be natural spoken text (will be sent to TTS)
- latex_elements must be valid LaTeX
- Return ONLY the JSON array
"""

    manifest_raw = call_model(
        role="M6",
        user_prompt=m6_prompt,
        expected_format="json_array",
        system_prompt_extra=(
            "You produce the final, clean Scene Manifest for a video pipeline. "
            "Your output must be perfect, valid JSON ready for downstream processing. "
            "Output ONLY the JSON array."
        ),
    )

    scene_manifest = extract_json(manifest_raw)
    logger.info(f"PHASE II COMPLETE: Scene Manifest with {len(scene_manifest)} scenes ready.")
    logger.info("")

    return scene_manifest
