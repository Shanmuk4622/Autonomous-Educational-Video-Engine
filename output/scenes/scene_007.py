from manim import *

class Scene007(Scene):
    def construct(self):
        eq = Text("c^2 = a^2 + b^2", font_size=48)
        self.play(Create(eq))
        self.wait(0.5)

        square_a = Text("a^2", font_size=24).move_to(LEFT*2)
        square_b = Text("b^2", font_size=24).move_to(RIGHT*2)
        self.play(Create(square_a), Create(square_b))
        self.wait(0.5)

        self.play(Transform(square_a, Text("a^2", font_size=24).move_to(UP*2 + LEFT*1).scale(0.7)),
                  Transform(square_b, Text("b^2", font_size=24).move_to(UP*2 + RIGHT*1).scale(0.7)))
        self.wait(0.5)

        rect = SurroundingRectangle(eq, color=YELLOW)
        self.play(Create(rect))
        self.wait(0.5)

        explanation = Text("Area of c^2 = a^2 + b^2", font_size=24).move_to(DOWN*2)
        self.play(Write(explanation))
        self.wait(0.5)

        self.play(*[FadeOut(mob) for mob in self.mobjects])