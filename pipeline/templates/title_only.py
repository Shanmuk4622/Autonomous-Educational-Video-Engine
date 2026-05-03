"""Layout: title_only.

A single phrase fills the frame — for opening, closing, or pivot beats.
Use TITLE_POS as the anchor; never place additional VMobjects.

Total runtime is partitioned 30% intro / 60% emphasis / 10% transition out.
Replace TITLE_TEXT and tune run_time literals to sum to your target T.
"""

from manim import FadeIn, FadeOut, Scene, Text

from output._style import ACCENT, BASE_FONT_SIZE, FONT, PRIMARY, TITLE_POS


class TitleOnly(Scene):
    def construct(self) -> None:
        title = Text(
            "TITLE_TEXT",
            font=FONT,
            font_size=int(BASE_FONT_SIZE * 1.4),
            color=PRIMARY,
        ).move_to(TITLE_POS)

        # intro
        self.play(FadeIn(title, shift=0.5), run_time=1.5)
        # emphasis: subtle color pulse
        self.play(title.animate.set_color(ACCENT), run_time=2.0)
        self.play(title.animate.set_color(PRIMARY), run_time=1.0)
        # transition out
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
