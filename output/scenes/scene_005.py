from manim import *

class Scene005(Scene):
    def construct(self):
        title = Text("Conclusion", font_size=40, color=YELLOW)
        self.play(Write(title))
        self.wait(0.5)
        self.play(title.animate.to_edge(UP).scale(0.7))

        eq = Text(r'\lim_{x \to 2} \frac{x^2 - 4}{x - 2} = \lim_{x \to 2} (x + 2)', font_size=36)
        self.play(Write(eq))
        self.wait(0.5)

        result = Text(r'= 4', font_size=36)
        self.play(Write(result))
        self.wait(0.5)
        self.play(result.animate.next_to(eq, RIGHT))

        arrow = Arrow(eq, result, buff=0.2)
        self.play(Create(arrow))
        self.wait(0.5)

        summary = Text(r'Therefore, we have shown that $\lim_{x \to 2} \frac{x^2 - 4}{x - 2} = \lim_{x \to 2} (x + 2) = 4$. This demonstrates that the original limit statement is true, with both sides of the equation approaching the value $4$ as $x$ approaches $2$.', font_size=24)
        self.play(FadeIn(summary))
        self.wait(0.5)
        self.play(summary.animate.shift(DOWN*2))

        self.wait(30.7)
        self.play(*[FadeOut(mob) for mob in self.mobjects])