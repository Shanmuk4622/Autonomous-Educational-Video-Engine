from manim import *

class Scene004(Scene):
    def construct(self):
        triangle = Polygon(ORIGIN, RIGHT*2, UP*2, color=BLUE)
        self.play(Create(triangle))
        self.wait(0.5)
        altitude = Line(ORIGIN, RIGHT*2, color=YELLOW)
        self.play(Create(altitude))
        self.wait(0.5)
        dot_d = Dot(RIGHT, color=RED)
        self.play(Create(dot_d))
        label_d = Text("D", font_size=24, color=RED).next_to(dot_d, RIGHT)
        self.play(Write(label_d))
        self.wait(0.5)
        explanatory_text = Text("Altitude divides triangle", font_size=24, color=YELLOW).to_edge(DOWN)
        self.play(FadeIn(explanatory_text))
        self.wait(5)