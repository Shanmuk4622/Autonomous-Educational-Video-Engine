from manim import *

class TestScene(Scene):
    def construct(self):
        circle = Circle(color=BLUE)
        self.play(Create(circle))
        self.wait(1)
        text = Text("Hello AEVE!", font_size=48)
        self.play(Write(text))
        self.wait(1)
