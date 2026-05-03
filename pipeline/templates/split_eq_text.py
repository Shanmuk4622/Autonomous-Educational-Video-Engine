"""Layout: split_eq_text.

Equation lives at LEFT_RAIL_POS, prose caption at RIGHT_RAIL_POS. Use this
when the visual is "the formula and what it means" side-by-side.

Pacing: 10% intro / 70% reveal both panels / 15% emphasis / 5% transition out.
Replace the equation LaTeX and the caption text. Keep MarkupText short
(≤ 3 lines) — long captions belong in narration, not on screen.
"""

from manim import (
    FadeOut,
    Indicate,
    MathTex,
    MarkupText,
    Scene,
    Write,
)

from output._style import (
    ACCENT,
    BASE_FONT_SIZE,
    LEFT_RAIL_POS,
    PRIMARY,
    RIGHT_RAIL_POS,
)


class SplitEqText(Scene):
    def construct(self) -> None:
        eq = MathTex("a^2 + b^2 = c^2", color=ACCENT).move_to(LEFT_RAIL_POS)
        caption = MarkupText(
            'Sum of squares\non legs equals\nsquare on hypotenuse',
            font_size=int(BASE_FONT_SIZE * 0.8),
            color=PRIMARY,
        ).move_to(RIGHT_RAIL_POS)

        # intro: equation
        self.play(Write(eq), run_time=2.0)
        # derivation: caption
        self.play(Write(caption), run_time=3.0)
        # emphasis
        self.play(Indicate(eq, color=ACCENT, scale_factor=1.1), run_time=1.0)
        self.play(Indicate(caption, color=ACCENT), run_time=1.0)
        # transition out
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
