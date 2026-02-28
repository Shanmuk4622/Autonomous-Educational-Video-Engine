from manim import *

class Scene005(Scene):
    def construct(self):
        area_formula = Text("A = \\frac{1}{2}bh", font_size=40)
        self.play(Create(area_formula))
        self.wait(0.5)

        area_triangle = Text("A = \\frac{1}{2}ab", font_size=40)
        self.play(Create(area_triangle))
        self.wait(0.5)

        triangle = Polygon(ORIGIN, RIGHT*2, UP*2, color=BLUE)
        altitude = Line(ORIGIN, UP*2, color=RED)
        self.play(Create(triangle), Create(altitude))
        self.wait(0.5)

        explanation = Text("Smaller triangles relate to sides.", font_size=30)
        explanation.next_to(triangle, DOWN)
        self.play(Create(explanation))
        self.wait(0.5)

        self.play(*[FadeOut(mob) for mob in self.mobjects])