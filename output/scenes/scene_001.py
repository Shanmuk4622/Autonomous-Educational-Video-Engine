from manim import *

class Scene001(Scene):
    def construct(self):
        title = Text("Introduction to the Pythagorean Theorem", font_size=40, color=YELLOW)
        self.play(Write(title))
        self.wait(0.5)
        self.play(title.animate.to_edge(UP).scale(0.7))

        triangle = Polygon(ORIGIN, RIGHT*2, UP*2, color=BLUE)
        self.play(Create(triangle))
        self.wait(0.5)

        a_label = Text("a", font_size=24).next_to(triangle, LEFT)
        b_label = Text("b", font_size=24).next_to(triangle, DOWN)
        c_label = Text("c", font_size=24).next_to(triangle, RIGHT)
        self.play(Write(a_label), Write(b_label), Write(c_label))
        self.wait(0.5)

        eq = Text("c^2 = a^2 + b^2", font_size=48)
        self.play(Write(eq))
        self.wait(0.5)

        explanation = Text("The Pythagorean theorem relates the lengths of the sides of a right-angled tr...", font_size=24)
        self.play(FadeIn(explanation))
        self.wait(0.5)

        self.wait(30)
        self.play(*[FadeOut(mob) for mob in self.mobjects])
        self.wait(35.7)