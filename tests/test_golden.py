"""
Golden frame regression test.

`tests/golden/pythagoras_60s/scene_003.py` is rendered with a fixed seed.
The frame at t=2.0s is compared against `expected.png` via PIL.ImageChops:
per-pixel tolerance 8/255, total mismatched-pixel ratio < 2%.

Until `expected.png` exists, the live test bootstraps it on first run
(when invoked with `--update-golden`). Until then the test skips.

Offline sanity (no manim required) verifies `scene_003.py` parses and
its AST-predicted runtime is in the expected band.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

GOLDEN_DIR = Path(__file__).parent / "golden" / "pythagoras_60s"
SCENE_PY = GOLDEN_DIR / "scene_003.py"
EXPECTED_PNG = GOLDEN_DIR / "expected.png"

PER_PIXEL_TOLERANCE = 8           # /255 channel
TOTAL_DIFF_BUDGET = 0.02          # 2% mismatched pixels allowed
FRAME_AT_S = 2.0                  # extract frame at this timestamp
RESOLUTION = (854, 480)           # render small for speed; matches -ql


# ---------------------------------------------------------------------------
# Pytest plumbing — `--update-golden` is registered in tests/conftest.py
# ---------------------------------------------------------------------------


@pytest.fixture
def update_golden(request) -> bool:
    return request.config.getoption("--update-golden", default=False)


# ---------------------------------------------------------------------------
# Offline sanity — runs in the default suite
# ---------------------------------------------------------------------------


def test_scene_source_parses():
    src = SCENE_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    assert "Scene003" in classes


def test_scene_runtime_in_band():
    """Sanity: scene_003.py is hand-tuned to ≈ 5.0s so frame-at-t=2.0s
    lands mid-derivation. Drift outside [4.6, 5.4] means the fixture has
    been edited and the golden frame is suspect."""
    from pipeline.timing import predict_manim_runtime

    pred = predict_manim_runtime(SCENE_PY.read_text(encoding="utf-8"))
    assert 4.6 <= pred.seconds <= 5.4, (
        f"scene_003.py predicted runtime {pred.seconds:.3f}s outside [4.6, 5.4]"
    )


# ---------------------------------------------------------------------------
# Live render + compare
# ---------------------------------------------------------------------------


def _run_manim(scene_py: Path, work_dir: Path) -> Path:
    """Render scene_003.py with manim CE 0.19 in low quality (854×480 @ 30fps).
    Returns the path of the rendered .mp4."""
    work_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "manim", "render",
        str(scene_py), "Scene003",
        "--fps", "30",
        "-r", f"{RESOLUTION[0]},{RESOLUTION[1]}",
        "--media_dir", str(work_dir),
        "-o", "scene_003",
        "--disable_caching",
        "--progress_bar", "none",
        "-v", "WARNING",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        raise RuntimeError(f"manim render failed: {res.stderr.strip()[-400:]}")
    out = work_dir / "videos" / scene_py.stem / f"{RESOLUTION[1]}p30" / "scene_003.mp4"
    if not out.exists():
        raise RuntimeError(f"manim succeeded but output missing: {out}")
    return out


def _extract_frame(mp4: Path, frame_at_s: float, png_out: Path) -> Path:
    """ffmpeg -ss <t> -i in.mp4 -vframes 1 out.png — single frame extract."""
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [
        ffmpeg, "-y",
        "-ss", f"{frame_at_s:.3f}",
        "-i", str(mp4),
        "-frames:v", "1",
        "-q:v", "2",
        str(png_out),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0 or not png_out.exists():
        raise RuntimeError(
            f"ffmpeg frame extract failed: {res.stderr.strip()[-300:]}"
        )
    return png_out


def _compare_pngs(expected: Path, actual: Path) -> tuple[float, int]:
    """Returns (mismatch_ratio, total_pixels) between two same-size PNGs.

    A pixel is "mismatched" if any of its R/G/B channels differ by more
    than PER_PIXEL_TOLERANCE.
    """
    from PIL import Image, ImageChops

    a = Image.open(expected).convert("RGB")
    b = Image.open(actual).convert("RGB")
    if a.size != b.size:
        raise AssertionError(
            f"PNG size mismatch: expected={a.size} actual={b.size}"
        )
    diff = ImageChops.difference(a, b)
    pixels = a.size[0] * a.size[1]
    # diff.getdata() yields (r, g, b) tuples — count any pixel with a
    # channel exceeding the tolerance.
    mismatched = sum(
        1 for px in diff.getdata() if max(px) > PER_PIXEL_TOLERANCE
    )
    return mismatched / pixels, pixels


@pytest.mark.live
def test_golden_frame_matches(update_golden, tmp_path: Path):
    """Render scene_003 and diff frame@t=2s against expected.png.

    With `--update-golden`, the rendered frame REPLACES expected.png so
    the next run will compare against it. Without that flag, missing
    expected.png is a skip (gives offline runs a clean exit).
    """
    if not EXPECTED_PNG.exists() and not update_golden:
        pytest.skip(
            f"golden expected.png missing at {EXPECTED_PNG}; run with "
            "`pytest -m live --update-golden` to bootstrap the fixture"
        )
    pytest.importorskip("PIL")

    work = tmp_path / "_golden_work"
    rendered_mp4 = _run_manim(SCENE_PY, work)
    actual_png = work / "frame.png"
    _extract_frame(rendered_mp4, FRAME_AT_S, actual_png)

    if update_golden or not EXPECTED_PNG.exists():
        # Bootstrap or refresh the golden fixture in place.
        shutil.copyfile(actual_png, EXPECTED_PNG)
        pytest.skip(
            f"golden frame written to {EXPECTED_PNG.relative_to(GOLDEN_DIR.parent.parent)}; "
            "rerun without --update-golden to verify"
        )

    ratio, total = _compare_pngs(EXPECTED_PNG, actual_png)
    assert ratio < TOTAL_DIFF_BUDGET, (
        f"golden frame mismatch: {ratio * 100:.2f}% pixels differ "
        f"(>{PER_PIXEL_TOLERANCE}/255 per channel, budget "
        f"{TOTAL_DIFF_BUDGET * 100:.0f}%, total pixels {total}). "
        f"Rerun with `--update-golden` if this is an intentional change."
    )
