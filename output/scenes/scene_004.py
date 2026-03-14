from manim import *

class Scene004(Scene):
    def construct(self):
        title = Text("Evaluating the Limit", font_size=40, color=YELLOW)
        self.play(Write(title))
        self.wait(0.5)
        self.play(title.animate.to_edge(UP).scale(0.7))

        eq = Text("$x + 2$", font_size=48)
        self.play(Write(eq))
        self.wait(0.5)

        x_val = Text("$x = 2$", font_size=36)
        self.play(Write(x_val))
        self.wait(0.5)

        new_eq = Text("$2 + 2$", font_size=48)
        self.play(Transform(eq, new_eq))
        self.wait(0.5)

        result = Text("$2 + 2 = 4$", font_size=36)
        self.play(FadeIn(result.next_to(eq, DOWN)))
        self.wait(0.5)

        limit_eq = Text("$\\lim_{x \\to 2} (x + 2) = 2 + 2 = 4$", font_size=36)
        self.play(FadeIn(limit_eq.next_to(result, DOWN)))
        self.wait(0.5)

        self.wait(24.0)
        self.play(*[FadeOut(mob) for mob in self.mobjects])
        self.wait(26.0)