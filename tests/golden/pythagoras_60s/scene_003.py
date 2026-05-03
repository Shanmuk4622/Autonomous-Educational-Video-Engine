"""
Golden frame source — scene 003 of the Pythagorean theorem walkthrough.

This file is hand-curated (not LLM-generated) so the golden frame remains
stable across model rolls. The test (`tests/test_golden.py`) renders this
scene with a fixed seed, extracts the frame at t=2.0s, and compares
against `expected.png` via PIL.ImageChops.

Layout: equation_focus. Total runtime: ~5.0s.
"""

from manim import (
    BLUE,
    DOWN,
    FadeOut,
    Indicate,
    MathTex,
    ReplacementTransform,
    Scene,
    UP,
    WHITE,
    Write,
)


class Scene003(Scene):
    def construct(self) -> None:
        # Build the three forms of the Pythagorean identity.
        eq_a = MathTex("a^2 + b^2 = c^2", color=WHITE).move_to(UP * 0.4)
        eq_b = MathTex("a^2 = c^2 - b^2", color=WHITE).move_to(UP * 0.4)
        eq_c = MathTex("a = \\sqrt{c^2 - b^2}", color=BLUE).move_to(UP * 0.4)

        # intro: write the identity (1.0s)
        self.play(Write(eq_a), run_time=1.0)
        # rewrite chain (1.5 + 1.5 = 3.0s) — frame at t=2.0s lands here,
        # mid-transition between eq_a and eq_b.
        self.play(ReplacementTransform(eq_a, eq_b), run_time=1.5)
        self.play(ReplacementTransform(eq_b, eq_c), run_time=1.5)
        # emphasis (0.5s)
        self.play(Indicate(eq_c, scale_factor=1.1, color=BLUE), run_time=0.5)
        # transition out (0.5s)
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
