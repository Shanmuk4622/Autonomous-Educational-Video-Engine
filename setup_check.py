"""
AEVE 2.0 — environment verifier.

Checks that the runtime dependencies AEVE needs are present and reachable on
PATH from inside `cv_conda`:

    - ffmpeg, ffprobe          (concat + duration probe; non-negotiable)
    - manim                    (CE 0.19.x; the actual renderer)
    - latex / xelatex          (for MathTex; soft requirement, fallback OK)
    - python                   (right version + right env)

Run modes:

    python setup_check.py                  # report-only; exit 1 on missing deps
    python setup_check.py --json           # machine-readable JSON to stdout
    python setup_check.py --install-miktex # opt-in: try conda, then winget

Importable:

    from setup_check import check_setup, SetupReport
    report = check_setup()
    if not report.ok():
        sys.exit(1)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
EXPECTED_CONDA_ENV = "cv_conda"

# Python is fine within [3.10, 3.13). Manim CE 0.19 is fussy past 3.12.
PYTHON_MIN = (3, 10)
PYTHON_MAX_EXCL = (3, 13)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ToolCheck:
    name: str
    found: bool
    path: str | None = None
    version: str | None = None
    note: str | None = None
    required: bool = True

    def status_line(self) -> str:
        marker = "OK   " if self.found else ("MISS " if self.required else "warn ")
        bits = [f"[{marker}] {self.name:<10}"]
        if self.version:
            bits.append(self.version)
        if self.path:
            bits.append(f"({self.path})")
        if self.note:
            bits.append(f"— {self.note}")
        return " ".join(bits)


@dataclass
class SetupReport:
    python: ToolCheck
    conda_env: ToolCheck
    ffmpeg: ToolCheck
    ffprobe: ToolCheck
    manim: ToolCheck
    latex: ToolCheck
    extra_warnings: list[str] = field(default_factory=list)

    def required_checks(self) -> list[ToolCheck]:
        return [self.python, self.conda_env, self.ffmpeg, self.ffprobe, self.manim]

    def optional_checks(self) -> list[ToolCheck]:
        return [self.latex]

    def all_checks(self) -> list[ToolCheck]:
        return self.required_checks() + self.optional_checks()

    def ok(self) -> bool:
        return all(c.found for c in self.required_checks())

    def to_dict(self) -> dict[str, object]:
        return {
            **{c.name: asdict(c) for c in self.all_checks()},
            "extra_warnings": self.extra_warnings,
            "ok": self.ok(),
        }


# ---------------------------------------------------------------------------
# Subprocess helpers — never raise; return None on failure
# ---------------------------------------------------------------------------


def _run_version(cmd: list[str], *, timeout_s: int = 10) -> tuple[str, str] | None:
    """Run `cmd` and return (stdout_first_line, full_stderr) or None on failure."""
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    out = (res.stdout or "").strip().splitlines()
    err = (res.stderr or "").strip()
    if res.returncode != 0 and not out and not err:
        return None
    first = out[0] if out else (err.splitlines()[0] if err else "")
    return first, err


# ---------------------------------------------------------------------------
# Individual probes
# ---------------------------------------------------------------------------


def check_python() -> ToolCheck:
    v = sys.version_info
    found = (v.major, v.minor) >= PYTHON_MIN and (v.major, v.minor) < PYTHON_MAX_EXCL
    note = None
    if not found:
        note = (
            f"need >= {PYTHON_MIN[0]}.{PYTHON_MIN[1]} and < "
            f"{PYTHON_MAX_EXCL[0]}.{PYTHON_MAX_EXCL[1]}"
        )
    return ToolCheck(
        name="python",
        found=found,
        path=sys.executable,
        version=f"{v.major}.{v.minor}.{v.micro}",
        note=note,
    )


def check_conda_env() -> ToolCheck:
    env = os.environ.get("CONDA_DEFAULT_ENV")
    if env is None:
        return ToolCheck(
            name="conda_env",
            found=False,
            note=f"CONDA_DEFAULT_ENV not set; run `conda activate {EXPECTED_CONDA_ENV}`",
        )
    if env != EXPECTED_CONDA_ENV:
        return ToolCheck(
            name="conda_env",
            found=False,
            version=env,
            note=f"active env is {env!r}, expected {EXPECTED_CONDA_ENV!r}",
        )
    return ToolCheck(name="conda_env", found=True, version=env)


def check_ffmpeg() -> ToolCheck:
    path = shutil.which("ffmpeg")
    if not path:
        return ToolCheck(
            name="ffmpeg",
            found=False,
            note="install via `conda install -n cv_conda -c conda-forge ffmpeg`",
        )
    res = _run_version([path, "-version"])
    version = res[0] if res else None
    return ToolCheck(name="ffmpeg", found=True, path=path, version=version)


def check_ffprobe() -> ToolCheck:
    path = shutil.which("ffprobe")
    if not path:
        return ToolCheck(
            name="ffprobe",
            found=False,
            note="usually installed alongside ffmpeg; check the same package",
        )
    res = _run_version([path, "-version"])
    version = res[0] if res else None
    return ToolCheck(name="ffprobe", found=True, path=path, version=version)


def check_manim() -> ToolCheck:
    """Prefer the `manim` CLI; fall back to `python -m manim` (works without
    the conda env on PATH, so it doesn't false-positive missing in IDEs/CI)."""
    path = shutil.which("manim")
    if path:
        res = _run_version([path, "--version"])
        version = (res[0] if res else "").strip() or None
    else:
        res = _run_version([sys.executable, "-m", "manim", "--version"])
        if res is None:
            return ToolCheck(
                name="manim",
                found=False,
                note="install via `pip install 'manim==0.19.*'` inside cv_conda",
            )
        # Last line is usually "Manim Community v0.19.1"; ignore prelim warnings.
        out_lines = [l for l in res[0].splitlines() if l.strip()]
        version = (out_lines[-1] if out_lines else res[0]).strip() or None
        path = f"{sys.executable} -m manim"

    note = None
    if version and "0.19" not in version:
        note = f"AEVE 2.0 pins 0.19.x; you have {version}"
    return ToolCheck(name="manim", found=True, path=path, version=version, note=note)


def check_latex() -> ToolCheck:
    """LaTeX is optional — we degrade to matplotlib MathTex when missing."""
    for binary in ("latex", "xelatex", "pdflatex"):
        path = shutil.which(binary)
        if path:
            res = _run_version([path, "--version"])
            version = res[0] if res else None
            return ToolCheck(
                name="latex",
                found=True,
                path=path,
                version=version,
                required=False,
            )
    return ToolCheck(
        name="latex",
        found=False,
        required=False,
        note=(
            "MathTex will fall back to matplotlib backend (lower quality). "
            "Install MiKTeX: `conda install -n cv_conda -c conda-forge miktex` "
            "or `winget install MiKTeX.MiKTeX`"
        ),
    )


def check_setup() -> SetupReport:
    return SetupReport(
        python=check_python(),
        conda_env=check_conda_env(),
        ffmpeg=check_ffmpeg(),
        ffprobe=check_ffprobe(),
        manim=check_manim(),
        latex=check_latex(),
    )


# ---------------------------------------------------------------------------
# Optional: opt-in MiKTeX install
# ---------------------------------------------------------------------------


def try_install_miktex() -> tuple[bool, str]:
    """Attempt MiKTeX install. Returns (success, log)."""
    log_lines: list[str] = []

    # 1. conda-forge
    log_lines.append("Attempting: conda install -n cv_conda -c conda-forge miktex -y")
    try:
        res = subprocess.run(
            ["conda", "install", "-n", EXPECTED_CONDA_ENV, "-c", "conda-forge", "miktex", "-y"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        log_lines.append(f"  rc={res.returncode}")
        log_lines.append(f"  stderr_tail: {(res.stderr or '').strip()[-300:]}")
        if res.returncode == 0:
            return True, "\n".join(log_lines)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log_lines.append(f"  conda install failed: {exc}")

    # 2. winget (Windows)
    if sys.platform.startswith("win"):
        log_lines.append("Attempting: winget install MiKTeX.MiKTeX --silent")
        try:
            res = subprocess.run(
                ["winget", "install", "MiKTeX.MiKTeX", "--silent", "--accept-source-agreements", "--accept-package-agreements"],
                capture_output=True,
                text=True,
                timeout=900,
            )
            log_lines.append(f"  rc={res.returncode}")
            if res.returncode == 0:
                return True, "\n".join(log_lines)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            log_lines.append(f"  winget install failed: {exc}")

    log_lines.append(
        "Both attempts failed. AEVE will run with the matplotlib MathTex fallback."
    )
    return False, "\n".join(log_lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_report(report: SetupReport) -> None:
    print("=" * 60)
    print("AEVE 2.0 environment check")
    print("=" * 60)
    for c in report.all_checks():
        print(c.status_line())
    for w in report.extra_warnings:
        print(f"[note ] {w}")
    print("-" * 60)
    print("RESULT:", "OK" if report.ok() else "MISSING REQUIRED DEPS")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify AEVE 2.0 runtime dependencies.",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--install-miktex",
        action="store_true",
        help="opt-in: try conda, then winget to install MiKTeX",
    )
    args = parser.parse_args()

    report = check_setup()

    if args.install_miktex and not report.latex.found:
        print("Installing MiKTeX (opt-in)…")
        ok, log = try_install_miktex()
        print(log)
        report.extra_warnings.append(
            "MiKTeX install attempted; rerun this script to verify."
        )
        if ok:
            # Re-run latex check
            report.latex = check_latex()

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_report(report)

    return 0 if report.ok() else 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "EXPECTED_CONDA_ENV",
    "SetupReport",
    "ToolCheck",
    "check_setup",
    "main",
    "try_install_miktex",
]
