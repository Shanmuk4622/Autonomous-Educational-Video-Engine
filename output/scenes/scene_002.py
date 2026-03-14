from manim import *

class Scene002(Scene):
    def construct(self):
        title = Text("Factoring the Numerator", font_size=40, color=YELLOW)
        self.play(Write(title))
        self.wait(0.5)
        self.play(title.animate.to_edge(UP).scale(0.7))

        eq = Text("$x^2 - 4 = (x + 2)(x - 2)$", font_size=48)
        self.play(Write(eq))
        self.wait(1)

        eq_transformed = Text("$x^2 - 4 = (x + 2)(x - 2)$", font_size=48)
        self.play(Transform(eq, eq_transformed))
        self.wait(0.5)

        new_eq = Text("$\\frac{(x + 2)(x - 2)}{x - 2}$", font_size=48)
        self.play(FadeIn(new_eq))
        self.play(new_eq.animate.next_to(eq, DOWN*2))
        self.wait(1)

        self.wait(22.4)
        self.play(*[FadeOut(mob) for mob in self.mobjects])
        self.wait(27.9)