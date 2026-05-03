"""
Tests for pipeline.timing — the AST runtime predictor.

ffprobe + ffmpeg pad/trim are integration-tested elsewhere (require real media
files). These unit tests cover the static analyzer that gates Animator output.
"""

from __future__ import annotations

import textwrap

import pytest

from pipeline.timing import (
    DEFAULT_PLAY_RUNTIME_S,
    DRIFT_BUDGET_S,
    predict_manim_runtime,
)


def test_predicts_explicit_run_times():
    code = textwrap.dedent(
        """
        from manim import *

        class S(Scene):
            def construct(self):
                self.play(FadeIn(Text("hi")), run_time=2.5)
                self.play(FadeOut(Text("hi")), run_time=1.0)
                self.wait(0.5)
        """
    )
    pred = predict_manim_runtime(code)
    assert pred.seconds == pytest.approx(4.0)
    assert pred.play_count == 2
    assert pred.wait_count == 1
    assert pred.used_default_play_runtime == 0


def test_falls_back_to_default_when_run_time_missing():
    code = textwrap.dedent(
        """
        from manim import *

        class S(Scene):
            def construct(self):
                self.play(FadeIn(Text("hi")))
                self.wait(0.5)
        """
    )
    pred = predict_manim_runtime(code)
    assert pred.seconds == pytest.approx(DEFAULT_PLAY_RUNTIME_S + 0.5)
    assert pred.used_default_play_runtime == 1


def test_handles_arithmetic_run_times():
    code = textwrap.dedent(
        """
        from manim import *

        class S(Scene):
            def construct(self):
                self.play(FadeIn(Text("hi")), run_time=1.5 + 0.5)
                self.wait(2.0 - 0.5)
        """
    )
    pred = predict_manim_runtime(code)
    assert pred.seconds == pytest.approx(2.0 + 1.5)


def test_in_window_check():
    code = "class S:\n    def construct(self):\n        self.play(x, run_time=10.0)\n"
    pred = predict_manim_runtime(code)
    assert pred.in_window(10.0)
    assert not pred.in_window(20.0)
    assert pred.in_window(9.7)  # within 0.92 lower bound


def test_unparseable_code_raises_syntax_error():
    with pytest.raises(SyntaxError):
        predict_manim_runtime("def construct(self:\n  oops")


def test_drift_budget_value():
    # Sanity check: the budget the rest of the pipeline gates on.
    assert DRIFT_BUDGET_S == 0.050
