from manim import *

class Scene003(Scene):
    def construct(self):
        eq = Text("c^2 = a^2 + b^2", font_size=48)
        self.play(Write(eq))
        self.wait(0.5)

        area_a = Text("a^2", font_size=36)
        area_a.next_to(eq, DOWN)
        area_b = Text("b^2", font_size=36)
        area_b.next_to(area_a, RIGHT*2)
        area_c = Text("c^2", font_size=36)
        area_c.next_to(eq, DOWN*2)
        self.play(Write(area_a), Write(area_b), Write(area_c))
        self.wait(0.5)

        rect_a = SurroundingRectangle(area_a, color=YELLOW)
        rect_b = SurroundingRectangle(area_b, color=YELLOW)
        rect_c = SurroundingRectangle(area_c, color=YELLOW)
        self.play(Create(rect_a), Create(rect_b), Create(rect_c))
        self.wait(0.5)

        explanation = Text("Area of square on side a is a^2", font_size=24)
        explanation.next_to(eq, DOWN*3)
        self.play(FadeIn(explanation))
        self.wait(0.5)

        self.play(*[FadeOut(mob) for mob in self.mobjects])