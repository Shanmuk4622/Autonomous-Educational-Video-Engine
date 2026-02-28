from manim import *

class Scene002(Scene):
    def construct(self):
        square_a = Square(side_length=2, color=BLUE)
        label_a = Text("$a^2$", font_size=24)
        label_a.next_to(square_a, DOWN)

        square_b = Square(side_length=3, color=RED)
        label_b = Text("$b^2$", font_size=24)
        label_b.next_to(square_b, DOWN)

        square_c = Square(side_length=3.6, color=GREEN)
        label_c = Text("$c^2$", font_size=24)
        label_c.next_to(square_c, DOWN)

        self.play(Create(square_a), Create(label_a))
        self.wait(0.5)
        self.play(Create(square_b), Create(label_b))
        self.wait(0.5)
        self.play(Create(square_c), Create(label_c))
        self.wait(0.5)

        triangle = Polygon(ORIGIN, RIGHT*2, UP*2, color=YELLOW)
        self.play(Create(triangle))
        self.wait(0.5)

        explanation = Text("Area of square: $s^2$", font_size=24)
        explanation.to_edge(DOWN)
        self.play(Write(explanation))
        self.wait(0.5)

        self.play(*[FadeOut(mob) for mob in self.mobjects])