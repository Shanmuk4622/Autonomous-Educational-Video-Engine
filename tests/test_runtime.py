"""
Tests for pipeline.runtime.emit_carry.

The helper duck-types for `.get_center()`, so we don't need real Manim
mobjects here — a simple stand-in class works.
"""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.runtime import emit_carry


class _FakeMobject:
    def __init__(self, name: str, position: tuple[float, float, float]):
        self._name = name
        self._pos = position

    def get_center(self):
        return self._pos


class _BadMobject:
    """No get_center → emit_carry should fall back to (0,0,0) without raising."""


def test_emit_carry_writes_canonical_path(tmp_path: Path):
    title = _FakeMobject("title", (0.0, 3.2, 0.0))
    out = emit_carry("001", {"title": title}, output_path=tmp_path)
    assert out == tmp_path / "scene_001.carry.json"
    assert out.exists()


def test_emit_carry_payload_round_trips(tmp_path: Path):
    title = _FakeMobject("title", (0.0, 3.2, 0.0))
    formula = _FakeMobject("eq", (1.5, -0.5, 0.0))
    out = emit_carry(
        "002",
        {"title": title, "formula": formula},
        output_path=tmp_path,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["scene_id"] == "002"
    assert {o["name"] for o in data["objects"]} == {"title", "formula"}
    title_obj = next(o for o in data["objects"] if o["name"] == "title")
    assert title_obj["position"] == [0.0, 3.2, 0.0]
    assert title_obj["kind"] == "_FakeMobject"


def test_emit_carry_handles_missing_get_center(tmp_path: Path):
    out = emit_carry("003", {"weird": _BadMobject()}, output_path=tmp_path)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["objects"][0]["position"] == [0.0, 0.0, 0.0]
    assert data["objects"][0]["kind"] == "_BadMobject"


def test_emit_carry_empty_mapping(tmp_path: Path):
    out = emit_carry("004", {}, output_path=tmp_path)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["objects"] == []


def test_emit_carry_creates_parent_dir(tmp_path: Path):
    """If the destination directory doesn't exist yet, it's created."""
    deep = tmp_path / "doesnt" / "exist" / "yet"
    out = emit_carry("005", {}, output_path=deep)
    assert out.parent == deep
    assert deep.exists()


def test_emit_carry_reads_back_via_carryover_helpers(tmp_path: Path):
    """End-to-end: emit_carry → read_carry round trip."""
    from pipeline.carryover import read_carry

    title = _FakeMobject("title", (0.0, 3.2, 0.0))
    emit_carry("006", {"title": title}, output_path=tmp_path)
    carry = read_carry(tmp_path, "006")
    assert carry.scene_id == "006"
    assert len(carry.objects) == 1
    assert carry.objects[0].name == "title"
    assert carry.objects[0].position == (0.0, 3.2, 0.0)
