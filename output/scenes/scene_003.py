from manim import *

class Scene003(Scene):
    def construct(self):
        title = Text("Canceling Common Factors", font_size=40, color=YELLOW)
        self.play(Write(title))
        self.wait(0.5)
        self.play(title.animate.to_edge(UP).scale(0.7))

        eq = Text(r"\frac{(x + 2)(x - 2)}{x - 2}", font_size=48)
        self.play(Write(eq))
        self.wait(0.5)

        strike = Line(start=eq.get_bottom() + DOWN*0.1, end=eq.get_top() + UP*0.1, stroke_width=3)
        strike.shift(RIGHT*1.5)
        self.play(Create(strike))
        self.wait(0.5)

        strike2 = Line(start=eq.get_bottom() + DOWN*0.1, end=eq.get_top() + UP*0.1, stroke_width=3)
        strike2.shift(RIGHT*2.5)
        self.play(Create(strike2))
        self.wait(0.5)

        new_eq = Text(r"x + 2", font_size=48)
        new_eq.next_to(eq, DOWN*2)
        self.play(FadeIn(new_eq))
        self.wait(0.5)

        self.wait(20)
        self.play(*[FadeOut(mob) for mob in self.mobjects])
        self.wait(25.9)