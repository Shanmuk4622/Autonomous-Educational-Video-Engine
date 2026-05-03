"""
Tests for setup_check.

The check_* functions probe the live host environment, so most of these
focus on report semantics + missing-tool simulation via monkeypatch on
`shutil.which`. We deliberately don't assert on whether THIS host has a
LaTeX install or a specific ffmpeg version.
"""

from __future__ import annotations

import sys

import setup_check
from setup_check import (
    SetupReport,
    ToolCheck,
    check_python,
    check_setup,
)


def test_tool_check_status_line_marker_required():
    c = ToolCheck(name="x", found=True, version="1.0")
    line = c.status_line()
    assert "OK" in line
    assert "1.0" in line


def test_tool_check_status_line_marker_missing_required():
    c = ToolCheck(name="x", found=False, required=True, note="install via foo")
    assert "MISS" in c.status_line()


def test_tool_check_status_line_optional_warn():
    c = ToolCheck(name="x", found=False, required=False)
    assert "warn" in c.status_line()


def test_check_python_accepts_current_runtime():
    c = check_python()
    # We're running under cv_conda's Python 3.10 — must be inside the band.
    assert c.found is True
    assert sys.version.startswith(c.version.split(".")[0])


def test_setup_report_ok_iff_all_required_found():
    r = SetupReport(
        python=ToolCheck("python", True, version="3.10.0"),
        conda_env=ToolCheck("conda_env", True, version="cv_conda"),
        ffmpeg=ToolCheck("ffmpeg", True),
        ffprobe=ToolCheck("ffprobe", True),
        manim=ToolCheck("manim", True),
        latex=ToolCheck("latex", False, required=False),
    )
    assert r.ok() is True

    r.ffmpeg = ToolCheck("ffmpeg", False)
    assert r.ok() is False


def test_setup_report_optional_latex_does_not_block_ok():
    r = SetupReport(
        python=ToolCheck("python", True, version="3.10.0"),
        conda_env=ToolCheck("conda_env", True, version="cv_conda"),
        ffmpeg=ToolCheck("ffmpeg", True),
        ffprobe=ToolCheck("ffprobe", True),
        manim=ToolCheck("manim", True),
        latex=ToolCheck("latex", False, required=False, note="not installed"),
    )
    assert r.ok() is True


def test_check_ffmpeg_missing_when_not_on_path(monkeypatch):
    """If shutil.which('ffmpeg') returns None, ffmpeg check must fail."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    c = setup_check.check_ffmpeg()
    assert c.found is False
    assert "conda install" in (c.note or "")


def test_check_ffprobe_missing_when_not_on_path(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    c = setup_check.check_ffprobe()
    assert c.found is False


def test_check_setup_returns_report():
    r = check_setup()
    assert isinstance(r, SetupReport)
    # Whatever the host state, the report is structured.
    assert r.python.name == "python"
    assert r.latex.required is False


def test_to_dict_serializable():
    r = SetupReport(
        python=ToolCheck("python", True, version="3.10.0"),
        conda_env=ToolCheck("conda_env", False),
        ffmpeg=ToolCheck("ffmpeg", True),
        ffprobe=ToolCheck("ffprobe", True),
        manim=ToolCheck("manim", True),
        latex=ToolCheck("latex", False, required=False),
    )
    d = r.to_dict()
    assert d["ok"] is False
    assert "python" in d
    assert d["latex"]["required"] is False
