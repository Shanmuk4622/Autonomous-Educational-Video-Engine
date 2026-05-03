"""
Golden frame regression test.

`tests/golden/pythagoras_60s/scene_003.py` is rendered with a fixed seed.
The frame at t=2.0s is compared against `expected.png` via PIL.ImageChops:
per-pixel tolerance 8/255, total mismatched-pixel ratio < 2%.

Until `expected.png` exists, this test is skipped (the very first live
render seeds it). Until then we still run a structural sanity check on
`scene_003.py` so the fixture itself doesn't bit-rot.

Pass `--update-golden` to ask the test runner to (re)generate
`expected.png`. That requires `manim` + `ffprobe` on PATH and is a live
test — gated under `pytest -m live`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

GOLDEN_DIR = Path(__file__).parent / "golden" / "pythagoras_60s"
SCENE_PY = GOLDEN_DIR / "scene_003.py"
EXPECTED_PNG = GOLDEN_DIR / "expected.png"

# Per-pixel tolerance and total-diff budget for the comparison.
PER_PIXEL_TOLERANCE = 8           # /255
TOTAL_DIFF_BUDGET = 0.02          # 2% of pixels


def pytest_addoption(parser):  # pragma: no cover — pytest plugin hook only
    """If you want to register --update-golden as a CLI flag, do it here."""


def test_scene_source_parses():
    """Sanity: scene_003.py is valid Python and declares Scene003."""
    src = SCENE_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    assert "Scene003" in classes


def test_scene_runtime_in_band():
    """Sanity: the scene's AST-predicted runtime ≈ 5s (the test grabs t=2.0s)."""
    from pipeline.timing import predict_manim_runtime

    pred = predict_manim_runtime(SCENE_PY.read_text(encoding="utf-8"))
    # Hand-crafted to land at exactly 5.0s
    assert 4.6 <= pred.seconds <= 5.4, (
        f"scene_003.py predicted runtime {pred.seconds:.3f}s outside [4.6, 5.4]"
    )


@pytest.mark.live
def test_golden_frame_matches():
    """Render scene_003 and diff frame@t=2s against expected.png.

    Skipped if expected.png isn't present — the first live render creates it.
    """
    if not EXPECTED_PNG.exists():
        pytest.skip(
            f"golden expected.png missing at {EXPECTED_PNG}; run a live "
            "render to bootstrap the fixture (pytest -m live --update-golden)"
        )

    pytest.importorskip("PIL")
    from PIL import Image, ImageChops  # noqa: F401

    # Live render path — Day 7+ wires this up. Until then we only assert
    # the fixture file exists if a maintainer dropped one in.
    pytest.skip(
        "live golden render harness lands in Day 7 — fixture file present "
        "but no comparison wiring yet"
    )
