"""Layout: title_plus_eq.

Short title at TITLE_POS, one equation at MAIN_POS. Use this when the scene
is "here is the claim" — title introduces a label, equation states it.

Pacing: 10% intro / 70% derivation / 15% emphasis / 5% transition out.
Replace TITLE_TEXT and the equation LaTeX. Keep both anchored to their zones.
"""

from manim import FadeIn, FadeOut, MathTex, Scene, Text, Write

from output._style import (
    ACCENT,
    BASE_FONT_SIZE,
    FONT,
    MAIN_POS,
    PRIMARY,
    TITLE_POS,
)


class TitlePlusEq(Scene):
    def construct(self) -> None:
        title = Text(
            "TITLE_TEXT",
            font=FONT,
            font_size=BASE_FONT_SIZE,
            color=PRIMARY,
        ).move_to(TITLE_POS)
        eq = MathTex("a^2 + b^2 = c^2", color=ACCENT).move_to(MAIN_POS)

        # intro
        self.play(FadeIn(title), run_time=1.0)
        # derivation: write the equation across most of the budget
        self.play(Write(eq), run_time=3.5)
        # emphasis
        self.play(eq.animate.scale(1.1), run_time=0.8)
        self.play(eq.animate.scale(1 / 1.1), run_time=0.4)
        # transition out
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
