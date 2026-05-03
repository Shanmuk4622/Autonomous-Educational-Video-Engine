"""
AEVE 2.0 — Manim source code sanitizer.

Replaces the legacy `sanitize_manim_code` (which crippled scenes by collapsing
every `MathTex` to `Text` and slicing strings at 80 chars). The new version
applies ONLY safe, idempotent transforms:

    Polygon([list])  →  Polygon(*list)     # spread args; preserves coords
    ShowCreation(x)  →  Create(x)          # CE 0.19 rename
    TextMobject(...) →  Text(...)          # CE 0.19 rename
    TexMobject(...)  →  MathTex(...)       # CE 0.19 rename

Anything else is left alone. The Animator's AST gates already reject the
forbidden names before we ever sanitize — sanitize is a belt for legacy /
LLM-trained-on-old-Manim drift.

Idempotency: running `safe_transform()` twice yields the same output as once.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("AEVE")


# ---------------------------------------------------------------------------
# Tracking — useful in tests and for repair-round telemetry
# ---------------------------------------------------------------------------


@dataclass
class SanitizeReport:
    polygon_spread: int = 0
    show_creation: int = 0
    text_mobject: int = 0
    tex_mobject: int = 0

    @property
    def total(self) -> int:
        return (
            self.polygon_spread
            + self.show_creation
            + self.text_mobject
            + self.tex_mobject
        )


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------


# Match `Polygon(<ws>[…])` where […] is a balanced bracket span (which may
# contain nested coord lists like `[[0,0,0], [1,0,0]]`). A simple
# `[^\[\]]*?` regex won't do because it can't span nested brackets.
_POLYGON_OPEN_RE = re.compile(r"\bPolygon\s*\(\s*\[")


def _replace_polygon(code: str, report: SanitizeReport) -> str:
    """Convert `Polygon([list])` → `Polygon(*list-spread)`.

    Scans with bracket-depth tracking so nested coord lists survive. If a
    given match isn't a single-list pattern (e.g. `Polygon(p, q)` already
    spread, or `Polygon([a], extra)`), we leave it alone.
    """
    out: list[str] = []
    i = 0
    n = len(code)
    while i < n:
        m = _POLYGON_OPEN_RE.search(code, i)
        if not m:
            out.append(code[i:])
            break
        # Emit prefix up to the match
        out.append(code[i:m.start()])
        bracket_start = m.end()  # index right after "["
        depth = 1
        j = bracket_start
        while j < n and depth > 0:
            ch = code[j]
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            # Unbalanced; bail this match and continue from after `Polygon(`
            out.append(code[m.start():m.end()])
            i = m.end()
            continue
        # j is index of the matching "]"; expect optional whitespace then ")"
        k = j + 1
        while k < n and code[k] in " \t":
            k += 1
        if k < n and code[k] == ")":
            inside = code[bracket_start:j]
            out.append(f"Polygon({inside})")
            report.polygon_spread += 1
            i = k + 1
        else:
            # Not the single-list pattern (e.g. Polygon([a], extra)); leave alone
            out.append(code[m.start():m.end()])
            i = m.end()
    return "".join(out)


def _word_replace(code: str, target: str, replacement: str) -> tuple[str, int]:
    """Replace `target` only when it appears as a whole identifier."""
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(target)}(?![A-Za-z0-9_])")
    new, n = pattern.subn(replacement, code)
    return new, n


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def safe_transform(code: str) -> tuple[str, SanitizeReport]:
    """Apply legacy-name fix-ups and Polygon spread to Manim source.

    Returns (new_code, report). `report.total == 0` means the source was
    already clean.
    """
    report = SanitizeReport()
    out = code

    out = _replace_polygon(out, report)

    out, n = _word_replace(out, "ShowCreation", "Create")
    report.show_creation = n
    out, n = _word_replace(out, "TextMobject", "Text")
    report.text_mobject = n
    out, n = _word_replace(out, "TexMobject", "MathTex")
    report.tex_mobject = n

    if report.total:
        logger.info(
            "[sanitize] applied %d safe transforms (poly=%d, ShowCreation=%d, TextMobject=%d, TexMobject=%d)",
            report.total,
            report.polygon_spread,
            report.show_creation,
            report.text_mobject,
            report.tex_mobject,
        )
    return out, report


__all__ = ["SanitizeReport", "safe_transform"]
