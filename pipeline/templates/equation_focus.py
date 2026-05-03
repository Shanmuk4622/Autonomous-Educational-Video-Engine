"""Layout: equation_focus.

A single equation at MAIN_POS, transformed step-by-step. No title.
Use this when each beat is a rewrite of the same identity.

Pacing: 10% intro / 70% rewrites / 15% emphasis / 5% transition out.
Replace the LaTeX in eq_a/eq_b/eq_c with your own forms; chain via
ReplacementTransform so the math morphs rather than cuts.
"""

from manim import FadeOut, Indicate, MathTex, ReplacementTransform, Scene, Write

from output._style import ACCENT, MAIN_POS, PRIMARY


class EquationFocus(Scene):
    def construct(self) -> None:
        eq_a = MathTex("a^2 + b^2 = c^2", color=PRIMARY).move_to(MAIN_POS)
        eq_b = MathTex("a^2 = c^2 - b^2", color=PRIMARY).move_to(MAIN_POS)
        eq_c = MathTex("a = \\sqrt{c^2 - b^2}", color=ACCENT).move_to(MAIN_POS)

        # intro
        self.play(Write(eq_a), run_time=2.0)
        # derivation
        self.play(ReplacementTransform(eq_a, eq_b), run_time=2.5)
        self.play(ReplacementTransform(eq_b, eq_c), run_time=2.5)
        # emphasis
        self.play(Indicate(eq_c, scale_factor=1.1, color=ACCENT), run_time=1.5)
        # transition out
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
