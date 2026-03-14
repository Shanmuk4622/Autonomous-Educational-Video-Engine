from manim import *

class Scene001(Scene):
    def construct(self):
        title = Text("Introduction to Limits", font_size=40, color=YELLOW)
        self.play(Write(title))
        self.wait(0.5)
        self.play(title.animate.to_edge(UP).scale(0.7))

        eq = Text("\\lim_{x \\to a} f(x)", font_size=48)
        self.play(Write(eq))
        self.wait(0.5)

        axes = Axes(x_range=[-5, 5], y_range=[-5, 5])
        self.play(Create(axes))

        func = lambda x: (x**2 - 4) / (x - 2) if x != 2 else 4
        graph = axes.plot(func, x_range=[-5, 5], color=BLUE)
        self.play(Create(graph))

        explanation = Text("The limit of a function $f(x)$ as $x$ approaches $a$ is denoted by $\\lim_{x ...", font_size=24)
        self.play(FadeIn(explanation))

        specific_eq = Text("\\lim_{x \\to 2} \\frac{x^2 - 4}{x - 2}", font_size=36)
        self.play(Write(specific_eq))
        self.wait(0.5)

        self.wait(29.2)
        self.play(*[FadeOut(mob) for mob in self.mobjects])