"""
Tests for renderer.assembler — the AEVE 2.0 normalize-then-concat path.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pipeline.schemas import FinalVideo, SceneAudio, SceneVideo
from renderer import assembler


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


def test_normalize_cmd_uses_canonical_video_params():
    cmd = assembler.build_normalize_cmd(Path("a.mp4"), Path("out.mp4"), fps=30)
    assert "fps=30,scale=1920:1080:flags=lanczos,setsar=1" in cmd
    assert "30000" in cmd  # video_track_timescale
    # Audio canonicalization
    assert "-ar" in cmd and "48000" in cmd
    assert "-ac" in cmd and "2" in cmd
    assert "-c:a" in cmd and "aac" in cmd


def test_normalize_cmd_never_uses_shortest():
    cmd = assembler.build_normalize_cmd(Path("a.mp4"), Path("out.mp4"))
    assert "-shortest" not in cmd


def test_concat_cmd_uses_demuxer_with_copy():
    cmd = assembler.build_concat_cmd(Path("list.txt"), Path("out.mp4"))
    assert "-f" in cmd and "concat" in cmd
    assert "-safe" in cmd and "0" in cmd
    assert "-c" in cmd and "copy" in cmd


def test_concat_cmd_never_uses_shortest():
    cmd = assembler.build_concat_cmd(Path("list.txt"), Path("out.mp4"))
    assert "-shortest" not in cmd


# ---------------------------------------------------------------------------
# Concat list writer
# ---------------------------------------------------------------------------


def test_write_concat_list_format(tmp_path: Path):
    clips = [tmp_path / "norm_001.mp4", tmp_path / "norm_002.mp4"]
    for c in clips:
        c.write_bytes(b"x")
    list_path = tmp_path / "concat.txt"
    assembler._write_concat_list(clips, list_path)
    text = list_path.read_text(encoding="utf-8")
    assert text.count("\n") == 2
    for line in text.splitlines():
        assert line.startswith("file '")
        assert line.endswith("'")


def test_write_concat_list_escapes_apostrophes(tmp_path: Path):
    weird = tmp_path / "it's a test.mp4"
    weird.write_bytes(b"x")
    list_path = tmp_path / "concat.txt"
    assembler._write_concat_list([weird], list_path)
    line = list_path.read_text(encoding="utf-8").strip()
    # ffmpeg concat-demuxer apostrophe escape: '\''
    assert r"'\''" in line


# ---------------------------------------------------------------------------
# assemble() with mocked normalize/concat/ffprobe
# ---------------------------------------------------------------------------


def _fake_video(scene_id: str, dur: float, mp4_path: Path) -> SceneVideo:
    return SceneVideo(
        scene_id=scene_id,
        mp4_path=mp4_path,
        measured_duration_s=dur,
        drift_ms=0,
    )


def _fake_audio(scene_id: str, dur: float, mp3_path: Path) -> SceneAudio:
    return SceneAudio(
        scene_id=scene_id,
        mp3_path=mp3_path,
        duration_s=dur,
        word_timeline=[],
        narration_final="x",
    )


def test_assemble_happy_path(monkeypatch, tmp_path: Path):
    # Set up two fake scene videos + audios on disk
    v1 = tmp_path / "scene_001.mp4"
    v1.write_bytes(b"v1")
    v2 = tmp_path / "scene_002.mp4"
    v2.write_bytes(b"v2")
    a1 = tmp_path / "scene_001.mp3"
    a1.write_bytes(b"a1")
    a2 = tmp_path / "scene_002.mp3"
    a2.write_bytes(b"a2")

    videos = [_fake_video("001", 5.0, v1), _fake_video("002", 4.0, v2)]
    audios = [_fake_audio("001", 5.0, a1), _fake_audio("002", 4.0, a2)]

    normalized_calls: list[Path] = []

    async def fake_normalize(in_mp4, out_mp4, *, fps):
        Path(out_mp4).write_bytes(b"normalized")
        normalized_calls.append(Path(in_mp4))
        return out_mp4

    async def fake_concat(concat_list, out_mp4):
        Path(out_mp4).write_bytes(b"final")
        return out_mp4

    async def fake_ffprobe(p):
        # Pretend the final is exactly the sum of audio durations.
        return 9.0

    monkeypatch.setattr(assembler, "_normalize", fake_normalize)
    monkeypatch.setattr(assembler, "_concat", fake_concat)
    monkeypatch.setattr(assembler, "ffprobe_duration", fake_ffprobe)

    final_dir = tmp_path / "final"
    work_dir = tmp_path / "work"

    final = asyncio.run(
        assembler.assemble(
            scene_videos=videos,
            scene_audios=audios,
            final_dir=final_dir,
            work_dir=work_dir,
        )
    )
    assert isinstance(final, FinalVideo)
    assert final.scene_count == 2
    assert final.total_duration_s == pytest.approx(9.0)
    assert final.total_drift_ms == 0
    assert final.mp4_path.exists()

    # Normalize was called once per scene, in scene-id order
    assert [p.name for p in normalized_calls] == ["scene_001.mp4", "scene_002.mp4"]


def test_assemble_logs_drift_above_budget_but_does_not_raise(
    monkeypatch, tmp_path: Path, caplog
):
    v1 = tmp_path / "v1.mp4"
    v1.write_bytes(b"x")
    a1 = tmp_path / "a1.mp3"
    a1.write_bytes(b"x")
    videos = [_fake_video("001", 5.0, v1)]
    audios = [_fake_audio("001", 5.0, a1)]

    async def fake_normalize(in_mp4, out_mp4, *, fps):
        Path(out_mp4).write_bytes(b"x")
        return out_mp4

    async def fake_concat(concat_list, out_mp4):
        Path(out_mp4).write_bytes(b"x")
        return out_mp4

    async def fake_ffprobe(p):
        return 5.500  # 500 ms over target → exceeds 50 ms budget

    monkeypatch.setattr(assembler, "_normalize", fake_normalize)
    monkeypatch.setattr(assembler, "_concat", fake_concat)
    monkeypatch.setattr(assembler, "ffprobe_duration", fake_ffprobe)

    final = asyncio.run(
        assembler.assemble(
            scene_videos=videos,
            scene_audios=audios,
            final_dir=tmp_path / "final",
            work_dir=tmp_path / "work",
        )
    )
    assert final.total_drift_ms == 500


def test_assemble_empty_videos_raises():
    with pytest.raises(ValueError, match="empty"):
        asyncio.run(
            assembler.assemble(
                scene_videos=[],
                scene_audios=[],
            )
        )
