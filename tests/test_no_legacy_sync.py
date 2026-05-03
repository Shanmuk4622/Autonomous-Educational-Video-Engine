"""
CI gate: AEVE 2.0 modules MUST NOT import the legacy `pipeline/sync_engine.py`.

The rewrite plan replaced the regex-based `self.wait()` patcher with the AST
runtime predictor (`pipeline/timing.py::predict_manim_runtime`). The legacy
file is preserved only because `pipeline/phase3_distributor.py` still uses
it during the side-by-side period — once that goes away, this test
loosens to "sync_engine.py does not exist."

If a future maintainer reintroduces a sync_engine import in any AEVE 2.0
module, this test fails loudly.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Modules that comprise AEVE 2.0 — none of these may import sync_engine.
AEVE_2_FILES = [
    "pipeline/orchestrator.py",
    "pipeline/solver.py",
    "pipeline/director.py",
    "pipeline/narrator.py",
    "pipeline/tts.py",
    "pipeline/animator.py",
    "pipeline/timing.py",
    "pipeline/style.py",
    "pipeline/schemas.py",
    "pipeline/carryover.py",
    "pipeline/runtime.py",
    "renderer/render.py",
    "renderer/healer.py",
    "renderer/sanitize.py",
    "renderer/assembler.py",  # tests for legacy alongside, but assemble() (2.0) must not depend
]

_SYNC_ENGINE_RE = re.compile(
    r"\b(from\s+pipeline\.sync_engine|import\s+pipeline\.sync_engine|"
    r"from\s+\.sync_engine|import\s+\.sync_engine)\b"
)


def test_aeve2_modules_do_not_import_sync_engine():
    offenders: list[tuple[str, int, str]] = []
    for relpath in AEVE_2_FILES:
        full = PROJECT_ROOT / relpath
        if not full.exists():
            continue
        for i, line in enumerate(full.read_text(encoding="utf-8").splitlines(), 1):
            if _SYNC_ENGINE_RE.search(line):
                offenders.append((relpath, i, line.strip()))

    assert not offenders, (
        "AEVE 2.0 modules must NOT import pipeline.sync_engine "
        "(replaced by pipeline.timing.predict_manim_runtime + "
        "renderer.render.pad_or_trim).\nOffenders:\n  " +
        "\n  ".join(f"{p}:{n}: {ln}" for p, n, ln in offenders)
    )


def test_assembler_2_0_function_does_not_call_sync_engine():
    """The new `assemble()` async function must not call into sync_engine
    even if the legacy `assemble_final_video` is still in the same module."""
    src = (PROJECT_ROOT / "renderer" / "assembler.py").read_text(encoding="utf-8")
    # Find the source of the async `assemble` function only. Signature may
    # span multiple lines, so we anchor on `async def assemble` and walk
    # to the next top-level def or __all__ block.
    match = re.search(
        r"async def assemble\b.*?(?=\nasync def |\ndef |\n__all__|\Z)",
        src,
        re.DOTALL,
    )
    assert match, "could not locate async def assemble(...) in assembler.py"
    body = match.group(0)
    assert "sync_engine" not in body, (
        "renderer.assembler.assemble() (AEVE 2.0) must not reference sync_engine"
    )
