"""
CI gate: legacy AEVE 1.0 files must stay deleted.

The rewrite plan replaced the regex-based `self.wait()` patcher
(`pipeline/sync_engine.py`) with the AST runtime predictor
(`pipeline/timing.py::predict_manim_runtime`). Day 7 retired the legacy
codebase entirely. This test guards against accidental resurrection.

If a future maintainer recreates `sync_engine.py` or any of the legacy
phase files, this test fails loudly. If they reintroduce an import of
`pipeline.sync_engine` in any AEVE 2.0 module, the second test fails.
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
    "renderer/assembler.py",
    "main.py",
    "app.py",
]

LEGACY_FILES_THAT_MUST_STAY_DELETED = [
    "pipeline/phase1_knowledge.py",
    "pipeline/phase2_committee.py",
    "pipeline/phase3_distributor.py",
    "pipeline/sync_engine.py",
    "pipeline/audio_stream.py",
    "pipeline/code_stream.py",
    "renderer/manim_runner.py",
    "models/llm_client.py",
    "models/__init__.py",
]

_SYNC_ENGINE_RE = re.compile(
    r"\b(from\s+pipeline\.sync_engine|import\s+pipeline\.sync_engine|"
    r"from\s+\.sync_engine|import\s+\.sync_engine)\b"
)

_LEGACY_MODELS_RE = re.compile(
    r"\b(from\s+models\b|import\s+models\b)"
)


def test_legacy_files_stay_deleted():
    """Day 7 retired these files. Recreating any of them is a regression."""
    resurrected = [
        p for p in LEGACY_FILES_THAT_MUST_STAY_DELETED
        if (PROJECT_ROOT / p).exists()
    ]
    assert not resurrected, (
        "Legacy AEVE 1.0 files must remain deleted (Day 7 retirement). "
        "Resurrected: " + ", ".join(resurrected)
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


def test_aeve2_modules_do_not_import_legacy_models_package():
    """`models.llm_client` is gone; AEVE 2.0 uses
    `pipeline.llm_clients.errors` + `logging.getLogger("AEVE")` directly."""
    offenders: list[tuple[str, int, str]] = []
    for relpath in AEVE_2_FILES:
        full = PROJECT_ROOT / relpath
        if not full.exists():
            continue
        for i, line in enumerate(full.read_text(encoding="utf-8").splitlines(), 1):
            if _LEGACY_MODELS_RE.search(line):
                offenders.append((relpath, i, line.strip()))

    assert not offenders, (
        "AEVE 2.0 modules must NOT import from the legacy `models/` package "
        "(retired in Day 7).\nOffenders:\n  " +
        "\n  ".join(f"{p}:{n}: {ln}" for p, n, ln in offenders)
    )


def test_assembler_does_not_reference_sync_engine():
    """The Phase 6 assembler is purely AEVE 2.0 — no sync_engine anywhere."""
    src = (PROJECT_ROOT / "renderer" / "assembler.py").read_text(encoding="utf-8")
    assert "sync_engine" not in src, (
        "renderer.assembler must not reference sync_engine"
    )
