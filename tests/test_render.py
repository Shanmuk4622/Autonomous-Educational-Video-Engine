"""
Tests for renderer.render — orchestration of manim subprocess + healer +
drift correction + audio mux. All subprocess calls are monkeypatched.

The actual ffprobe/ffmpeg/manim binaries aren't required for this suite.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pipeline.schemas import SceneAudio, SceneCode, SceneVideo, WordEvent
from renderer import healer as healer_mod
from renderer import render as render_mod
from renderer.render import RenderConfig, RenderError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _audio(scene_id: str = "001", dur: float = 5.0, mp3_path: Path | None = None) -> SceneAudio:
    return SceneAudio(
        scene_id=scene_id,
        mp3_path=mp3_path or Path(f"scene_{scene_id}.mp3"),
        duration_s=dur,
        word_timeline=[WordEvent(word="x", start_s=0.0, end_s=0.5)],
        narration_final="x",
    )


def _code(scene_id: str, py_path: Path) -> SceneCode:
    return SceneCode(
        scene_id=scene_id,
        py_path=py_path,
        class_name=f"Scene{scene_id}",
        target_runtime_s=5.0,
        ast_validated=True,
        predicted_runtime_s=5.0,
    )


# ---------------------------------------------------------------------------
# _tail_bytes
# ---------------------------------------------------------------------------


def test_tail_bytes_under_limit_passthrough():
    assert render_mod._tail_bytes("hello", limit=100) == "hello"


def test_tail_bytes_truncates_to_last_N():
    text = "x" * 6000
    out = render_mod._tail_bytes(text, limit=4096)
    assert len(out.encode("utf-8")) <= 4096


def test_tail_bytes_handles_none():
    assert render_mod._tail_bytes(None) == ""


# ---------------------------------------------------------------------------
# _manim_cmd
# ---------------------------------------------------------------------------


def test_manim_cmd_uses_python_dash_m():
    cmd = render_mod._manim_cmd(
        py_path=Path("scene_001.py"),
        class_name="Scene001",
        media_dir=Path("media"),
        output_basename="scene_001_silent",
        fps=30,
        resolution="1920,1080",
    )
    # First two args are the Python interpreter + -m
    assert cmd[1] == "-m"
    assert cmd[2] == "manim"
    assert "render" in cmd
    assert "Scene001" in cmd
    assert "--fps" in cmd
    assert "1920,1080" in cmd
    # Progress bars off (Windows stdout safety)
    assert "--progress_bar" in cmd
    assert "none" in cmd


def test_manim_cmd_disables_caching():
    cmd = render_mod._manim_cmd(
        py_path=Path("a.py"),
        class_name="X",
        media_dir=Path("m"),
        output_basename="o",
        fps=30,
        resolution="1920,1080",
    )
    assert "--disable_caching" in cmd


# ---------------------------------------------------------------------------
# Audio mux command shape
# ---------------------------------------------------------------------------


def test_mux_never_uses_shortest(monkeypatch, tmp_path: Path):
    """Spy on subprocess.run inside _mux_sync to confirm `-shortest` is absent."""
    captured_cmds: list[list[str]] = []

    class _FakeRes:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        captured_cmds.append(cmd)
        return _FakeRes()

    monkeypatch.setattr(render_mod.subprocess, "run", fake_run)

    silent = tmp_path / "v.mp4"
    silent.write_bytes(b"x")
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    out = tmp_path / "o.mp4"

    render_mod._mux_sync(silent, audio, out)
    assert captured_cmds, "ffmpeg subprocess should have been invoked"
    cmd = captured_cmds[0]
    assert "-shortest" not in cmd
    # Audio re-encoded to AAC 192k 48k stereo
    assert "aac" in cmd
    assert "192k" in cmd
    assert "48000" in cmd


# ---------------------------------------------------------------------------
# render_scene — happy path (first attempt succeeds)
# ---------------------------------------------------------------------------


def test_render_scene_happy_path(monkeypatch, tmp_path: Path):
    py = tmp_path / "scene_001.py"
    py.write_text("# scene\n", encoding="utf-8")
    code = _code("001", py)
    audio = _audio("001", dur=5.0, mp3_path=tmp_path / "a.mp3")
    audio.mp3_path.write_bytes(b"x")

    rendered_path = tmp_path / "rendered.mp4"

    async def fake_run_manim(**kwargs):
        rendered_path.write_bytes(b"silent")
        return rendered_path

    async def fake_pad_or_trim(mp4, target_s, **kwargs):
        from pipeline.timing import CorrectionResult
        return CorrectionResult(action="noop", final_duration_s=target_s, delta_s=0.0)

    async def fake_mux(silent, mp3, out):
        out.write_bytes(b"final")

    async def fake_ffprobe(p):
        return 5.000

    monkeypatch.setattr(render_mod, "_run_manim", fake_run_manim)
    monkeypatch.setattr(render_mod, "pad_or_trim", fake_pad_or_trim)
    monkeypatch.setattr(render_mod, "_mux", fake_mux)
    monkeypatch.setattr(render_mod, "ffprobe_duration", fake_ffprobe)

    result = asyncio.run(
        render_mod.render_scene(code=code, audio=audio, video_dir=tmp_path / "video")
    )
    assert isinstance(result, SceneVideo)
    assert result.scene_id == "001"
    assert result.used_healer is False
    assert result.healer_attempts == 0
    assert result.drift_ms == 0
    assert result.measured_duration_s == pytest.approx(5.0)
    assert result.mp4_path.exists()


# ---------------------------------------------------------------------------
# render_scene — first attempt fails, healer recovers
# ---------------------------------------------------------------------------


def test_render_scene_healer_recovers(monkeypatch, tmp_path: Path):
    py = tmp_path / "scene_001.py"
    py.write_text("# broken\n", encoding="utf-8")
    code = _code("001", py)
    audio = _audio("001", dur=5.0, mp3_path=tmp_path / "a.mp3")
    audio.mp3_path.write_bytes(b"x")

    attempt = {"n": 0}

    async def fake_run_manim(**kwargs):
        attempt["n"] += 1
        if attempt["n"] == 1:
            raise RenderError(
                "boom", stderr_tail="ImportError: bad module", returncode=1
            )
        out = tmp_path / "rendered.mp4"
        out.write_bytes(b"silent")
        return out

    async def fake_heal(*, broken_code, stderr_tail, target_runtime_s, scene_id, style=None):
        # Verify the renderer passed us the failing source + stderr
        assert "ImportError" in stderr_tail
        return "# healed code\n"

    async def fake_pad_or_trim(mp4, target_s, **kwargs):
        from pipeline.timing import CorrectionResult
        return CorrectionResult(action="noop", final_duration_s=target_s, delta_s=0.0)

    async def fake_mux(silent, mp3, out):
        out.write_bytes(b"final")

    async def fake_ffprobe(p):
        return 5.0

    monkeypatch.setattr(render_mod, "_run_manim", fake_run_manim)
    monkeypatch.setattr(healer_mod, "heal", fake_heal)
    monkeypatch.setattr(render_mod, "pad_or_trim", fake_pad_or_trim)
    monkeypatch.setattr(render_mod, "_mux", fake_mux)
    monkeypatch.setattr(render_mod, "ffprobe_duration", fake_ffprobe)

    result = asyncio.run(
        render_mod.render_scene(code=code, audio=audio, video_dir=tmp_path / "video")
    )
    assert result.used_healer is True
    assert result.healer_attempts == 1
    # Healer's text was written to py_path
    assert py.read_text(encoding="utf-8") == "# healed code\n"
    # Backup of the failing attempt was created
    assert py.with_suffix(".attempt_1.py.bak").exists()


# ---------------------------------------------------------------------------
# render_scene — all attempts fail, deterministic fallback used
# ---------------------------------------------------------------------------


def test_render_scene_falls_back_to_deterministic_template(monkeypatch, tmp_path: Path):
    py = tmp_path / "scene_001.py"
    py.write_text("# broken\n", encoding="utf-8")
    code = _code("001", py)
    audio = _audio("001", dur=5.0, mp3_path=tmp_path / "a.mp3")
    audio.mp3_path.write_bytes(b"x")

    manim_calls = {"n": 0}

    async def fake_run_manim(**kwargs):
        manim_calls["n"] += 1
        # Fail every attempt EXCEPT the very last call (after fallback)
        if manim_calls["n"] <= 4:  # cfg.max_attempts default = 4
            raise RenderError(
                "boom", stderr_tail="generic boom", returncode=1
            )
        out = tmp_path / "rendered.mp4"
        out.write_bytes(b"silent")
        return out

    async def fake_heal(**kwargs):
        return "# heal text\n"

    fallback_called = {"n": 0}

    def fake_write_fallback(*, py_path, scene_id, title, formulas, target_runtime_s):
        fallback_called["n"] += 1
        Path(py_path).write_text("# fallback\n", encoding="utf-8")
        return Path(py_path)

    async def fake_pad_or_trim(mp4, target_s, **kwargs):
        from pipeline.timing import CorrectionResult
        return CorrectionResult(action="noop", final_duration_s=target_s, delta_s=0.0)

    async def fake_mux(silent, mp3, out):
        out.write_bytes(b"final")

    async def fake_ffprobe(p):
        return 5.0

    monkeypatch.setattr(render_mod, "_run_manim", fake_run_manim)
    monkeypatch.setattr(healer_mod, "heal", fake_heal)
    monkeypatch.setattr(healer_mod, "write_fallback_scene", fake_write_fallback)
    monkeypatch.setattr(render_mod, "pad_or_trim", fake_pad_or_trim)
    monkeypatch.setattr(render_mod, "_mux", fake_mux)
    monkeypatch.setattr(render_mod, "ffprobe_duration", fake_ffprobe)

    result = asyncio.run(
        render_mod.render_scene(
            code=code,
            audio=audio,
            video_dir=tmp_path / "video",
            cfg=RenderConfig(max_attempts=4),
        )
    )
    assert result.used_healer is True
    assert fallback_called["n"] == 1
    assert result.mp4_path.exists()
