"""Layout: graph.

A coordinate plane fills the frame; one or two FunctionGraphs animate on top.
Title sits at TITLE_POS as a label.

Pacing: 15% intro (axes) / 60% draw curves / 20% emphasis / 5% transition out.
Replace the function lambdas, axis ranges, and TITLE_TEXT.
"""

from manim import (
    Axes,
    Create,
    FadeOut,
    Scene,
    Text,
    Write,
)

from output._style import ACCENT, BASE_FONT_SIZE, FONT, PRIMARY, TITLE_POS


class GraphScene(Scene):
    def construct(self) -> None:
        title = Text(
            "TITLE_TEXT",
            font=FONT,
            font_size=BASE_FONT_SIZE,
            color=PRIMARY,
        ).move_to(TITLE_POS)
        axes = Axes(
            x_range=[-3, 3, 1],
            y_range=[-2, 4, 1],
            x_length=8,
            y_length=4.5,
            tips=False,
            axis_config={"color": PRIMARY},
        )
        curve = axes.plot(lambda x: x**2, color=ACCENT)
        label = axes.get_graph_label(curve, label="y = x^2").set_color(ACCENT)

        # intro: title + axes
        self.play(Write(title), Create(axes), run_time=1.5)
        # derivation: draw curve + label
        self.play(Create(curve), run_time=2.5)
        self.play(Write(label), run_time=1.5)
        # emphasis: gentle highlight pass
        self.play(curve.animate.set_stroke(width=6), run_time=1.0)
        self.play(curve.animate.set_stroke(width=4), run_time=0.5)
        # transition out
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
