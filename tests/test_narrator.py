"""
Unit tests for pipeline.narrator — output validator only (no live calls).
"""

from __future__ import annotations

import pytest

from pipeline.narrator import MAX_NARRATION_CHARS, _validate


def test_validate_accepts_clean_prose():
    text = "We start with a right triangle. Its legs have lengths a and b."
    assert _validate(text) == text


def test_validate_strips_surrounding_quotes():
    assert _validate('"a squared plus b squared."') == "a squared plus b squared."
    assert _validate("'a squared.'") == "a squared."


def test_validate_truncates_long_output():
    long = ("This is a sentence. " * 50).strip()
    out = _validate(long)
    assert len(out) <= MAX_NARRATION_CHARS


def test_validate_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        _validate("   ")


def test_validate_rejects_latex_residue_command():
    with pytest.raises(ValueError, match="LaTeX residue"):
        _validate("the value of \\frac{a}{b} is computed.")


def test_validate_rejects_dollar_signs():
    with pytest.raises(ValueError, match="LaTeX residue"):
        _validate("the formula $a+b$ is shown.")


def test_validate_rejects_caret():
    with pytest.raises(ValueError, match="LaTeX residue"):
        _validate("a^2 is read as a squared.")
