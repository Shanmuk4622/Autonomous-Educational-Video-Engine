"""Layout: derivation_chain.

Vertical column of equations at MAIN_POS — each step appears below the prior,
forming a derivation. Use VGroup.arrange(DOWN) so spacing stays uniform.

Pacing: 10% intro / 75% step-in (split evenly across N rows) / 10% emphasis /
5% transition out.
Replace the LaTeX rows; aim for 3-5 rows total.
"""

from manim import (
    DOWN,
    FadeIn,
    FadeOut,
    Indicate,
    MathTex,
    Scene,
    VGroup,
    Write,
)

from output._style import ACCENT, MAIN_POS, PRIMARY


class DerivationChain(Scene):
    def construct(self) -> None:
        rows = VGroup(
            MathTex("a^2 + b^2 = c^2", color=PRIMARY),
            MathTex("a^2 = c^2 - b^2", color=PRIMARY),
            MathTex("a = \\sqrt{c^2 - b^2}", color=ACCENT),
        ).arrange(DOWN, buff=0.5).move_to(MAIN_POS)

        # intro: first row
        self.play(Write(rows[0]), run_time=1.5)
        # derivation: cascade remaining rows
        for row in rows[1:]:
            self.play(FadeIn(row, shift=0.3), run_time=2.0)
        # emphasis on the final result
        self.play(Indicate(rows[-1], color=ACCENT, scale_factor=1.1), run_time=1.0)
        # transition out
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
