"""
Phase I: Knowledge Distillation — M1 (Problem Solver) + M2 (Solution Verifier)

Takes a text query (or image path) and produces Script 1: a "Deep Solution" document
with step-by-step math in strict LaTeX notation.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.llm_client import call_model, logger


def run_phase1(query: str, image_path: str = None) -> str:
    """
    Phase I: Knowledge Distillation

    1. M1 (Gemini Flash) solves the problem with full LaTeX
    2. M2 (Groq Llama 70B) verifies and enriches the solution

    Returns:
        script_1 (str): The Deep Solution document
    """
    logger.info("=" * 60)
    logger.info("PHASE I: KNOWLEDGE DISTILLATION")
    logger.info("=" * 60)

    # ── Step 1: M1 — Problem Solver ─────────────────────────────
    logger.info("Step 1/2: M1 (Problem Solver) — Solving the input query...")

    m1_prompt = f"""You are given the following educational/mathematical query. Solve it completely.

QUERY: {query}

YOUR TASK:
1. Start with clear DEFINITIONS of all relevant concepts.
2. Provide a complete, step-by-step mathematical solution.
3. Show ALL intermediate steps — do not skip any algebra.
4. Include conceptual explanations for WHY each step works.
5. End with a clear CONCLUSION summarizing the result.

FORMATTING RULES:
- ALL math must use LaTeX: $inline$ or $$display$$.
- Number each step clearly (Step 1, Step 2, ...).
- Use proper LaTeX: \\frac{{}}{{}}, \\sqrt{{}}, \\int, \\sum, \\lim, etc.
- This will be used to create an animated educational video, so be thorough and pedagogical.

OUTPUT FORMAT:
Return a well-structured document with sections:
## Definitions
## Solution
### Step 1: [description]
### Step 2: [description]
...
## Conclusion
"""

    raw_solution = call_model(
        role="M1",
        user_prompt=m1_prompt,
        expected_format="latex_rich",
        image_path=image_path,
        system_prompt_extra=(
            "You produce educational mathematical content. "
            "Your output will be used to generate an animated video. "
            "Be thorough, precise, and pedagogical. "
            "EVERY mathematical expression must be in LaTeX notation."
        ),
    )

    logger.info(f"  M1 produced a solution ({len(raw_solution)} chars)")

    # ── Step 2: M2 — Solution Verifier ──────────────────────────
    logger.info("Step 2/2: M2 (Solution Verifier) — Verifying and enriching...")

    m2_prompt = f"""You are a mathematical verification expert. Review the following solution for CORRECTNESS and COMPLETENESS.

ORIGINAL QUERY: {query}

PROPOSED SOLUTION:
{raw_solution}

YOUR TASK:
1. CHECK every mathematical step for correctness. If you find ANY error, FIX it.
2. VERIFY the final answer is correct.
3. ADD any missing intermediate steps that would help a student understand.
4. ENSURE every equation is in proper LaTeX format ($..$ or $$..$$).
5. IMPROVE the pedagogical flow — make it suitable for a visual video explanation.

OUTPUT REQUIREMENTS:
- Return the COMPLETE corrected/verified solution (not just the corrections).
- Keep the same structure: Definitions → Solution → Conclusion.
- If everything is correct, return the solution enriched with better explanations.
- ALL math in LaTeX notation. No plain-text math ever.
- Mark the solution as "VERIFIED ✓" at the top if it is correct.
"""

    verified_solution = call_model(
        role="M2",
        user_prompt=m2_prompt,
        expected_format="latex_rich",
        system_prompt_extra=(
            "You are a mathematical verification expert. "
            "Your job is to guarantee 100% correctness. "
            "If there's any doubt, re-derive the result yourself. "
            "Return the FULL verified solution, not just corrections."
        ),
    )

    logger.info(f"  M2 verified solution ({len(verified_solution)} chars)")
    logger.info("PHASE I COMPLETE: Script 1 (Deep Solution) ready.")
    logger.info("")

    return verified_solution
