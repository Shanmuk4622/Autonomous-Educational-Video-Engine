"""
AEVE 2.0 — environment bootstrap.

Prepends the running Python's neighbouring `Scripts` and `Library/bin`
directories to PATH so subprocesses (Manim → latex.exe → dvisvgm.exe; ffmpeg;
ffprobe) find conda-installed binaries even when `conda activate cv_conda`
was not run in the parent shell. Idempotent — safe to call repeatedly.

Imported for side effect by `pipeline/__init__.py`, so any `from pipeline.X
import …` automatically fixes up PATH before subprocess work begins.
"""

from __future__ import annotations

import os
import sys


def ensure_conda_bin_on_path() -> list[str]:
    """Prepend conda env bin directories to PATH. Returns the list actually
    inserted (in insertion order)."""
    py_dir = os.path.dirname(sys.executable)
    candidates = [
        os.path.join(py_dir, "Scripts"),         # Windows conda: latex, dvisvgm
        os.path.join(py_dir, "Library", "bin"),  # Windows conda: ffmpeg, dlls
        py_dir,                                   # POSIX: env root holds bin dir
    ]
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    inserted: list[str] = []
    for c in candidates:
        if os.path.isdir(c) and c not in parts:
            parts.insert(0, c)
            inserted.append(c)
    if inserted:
        os.environ["PATH"] = os.pathsep.join(parts)
    return inserted


# Run on import so any process that imports `pipeline.*` gets the fix-up
# before it spawns subprocesses.
ensure_conda_bin_on_path()


__all__ = ["ensure_conda_bin_on_path"]
