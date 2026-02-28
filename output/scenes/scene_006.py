from manim import *

class Scene006(Scene):
    def construct(self):
        self.wait(0.5)

        eq = Text("c^2 = a^2 + b^2", font_size=48)
        self.play(Write(eq))
        self.wait(0.5)

        square_a = Text("a^2", font_size=24).next_to(eq, DOWN)
        square_b = Text("b^2", font_size=24).next_to(square_a, RIGHT)
        square_c = Text("c^2", font_size=24).next_to(eq, UP)
        self.play(Write(square_a), Write(square_b), Write(square_c))
        self.wait(0.5)

        self.play(Transform(square_a, Text("a^2", font_size=24).next_to(square_c, LEFT)),
                 Transform(square_b, Text("b^2", font_size=24).next_to(square_c, RIGHT)))
        self.wait(0.5)

        rect = SurroundingRectangle(eq, color=YELLOW)
        self.play(Create(rect))
        self.wait(0.5)

        explanation = Text("The area of the square on the hypotenuse is equal to the sum of the areas of ...", font_size=24).next_to(eq, DOWN*2)
        self.play(Write(explanation))
        self.wait(0.5)

        self.play(*[FadeOut(mob) for mob in self.mobjects])
        self.wait(37.6)