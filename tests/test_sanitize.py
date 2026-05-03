"""
Tests for renderer.sanitize — only the four safe transforms; idempotency.
"""

from __future__ import annotations

from renderer.sanitize import safe_transform


def test_show_creation_renamed():
    src = "self.play(ShowCreation(circle))\n"
    out, report = safe_transform(src)
    assert out == "self.play(Create(circle))\n"
    assert report.show_creation == 1
    assert report.total == 1


def test_text_mobject_renamed():
    src = "title = TextMobject('Hello')\n"
    out, report = safe_transform(src)
    assert "Text('Hello')" in out
    assert report.text_mobject == 1


def test_tex_mobject_renamed():
    src = "eq = TexMobject('a^2')\n"
    out, report = safe_transform(src)
    assert "MathTex('a^2')" in out
    assert report.tex_mobject == 1


def test_polygon_list_spread():
    src = "p = Polygon([[0,0,0], [1,0,0], [1,1,0]])\n"
    out, report = safe_transform(src)
    assert out == "p = Polygon([0,0,0], [1,0,0], [1,1,0])\n"
    assert report.polygon_spread == 1


def test_word_boundary_does_not_eat_substrings():
    # `MyShowCreation` should NOT become `MyCreate`
    src = "x = MyShowCreation()\n"
    out, report = safe_transform(src)
    assert out == src
    assert report.total == 0


def test_no_transforms_needed_is_noop():
    src = (
        "from manim import Scene, Create, Text, MathTex\n"
        "class S(Scene):\n"
        "    def construct(self):\n"
        "        self.play(Create(Text('x')))\n"
    )
    out, report = safe_transform(src)
    assert out == src
    assert report.total == 0


def test_idempotent():
    src = "self.play(ShowCreation(c))\nlabel = TextMobject('y')\n"
    once, _ = safe_transform(src)
    twice, report2 = safe_transform(once)
    assert twice == once
    assert report2.total == 0


def test_multiple_transforms_in_one_pass():
    src = (
        "from manim import *\n"
        "self.play(ShowCreation(c))\n"
        "self.play(ShowCreation(d))\n"
        "t = TextMobject('hi')\n"
        "e = TexMobject('a^2')\n"
        "p = Polygon([[0,0,0],[1,0,0]])\n"
    )
    out, report = safe_transform(src)
    assert "ShowCreation" not in out
    assert "TextMobject" not in out
    assert "TexMobject" not in out
    # The outer wrapping list was stripped; nested coord lists remain.
    assert "Polygon([[" not in out
    assert "Polygon([0,0,0],[1,0,0])" in out
    assert report.show_creation == 2
    assert report.text_mobject == 1
    assert report.tex_mobject == 1
    assert report.polygon_spread == 1
    assert report.total == 5
