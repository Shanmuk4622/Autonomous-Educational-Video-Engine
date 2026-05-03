"""
Tests for pipeline.carryover — read/write SceneCarry JSON.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.carryover import (
    carry_path,
    empty_carry,
    read_carry,
    write_carry,
)
from pipeline.schemas import CarryObject, SceneCarry


def test_carry_path_canonical(tmp_path: Path):
    p = carry_path(tmp_path, "002")
    assert p.name == "scene_002.carry.json"
    assert p.parent == tmp_path


def test_read_missing_returns_empty(tmp_path: Path):
    carry = read_carry(tmp_path, "001")
    assert isinstance(carry, SceneCarry)
    assert carry.scene_id == "001"
    assert carry.objects == []


def test_write_then_read_roundtrip(tmp_path: Path):
    original = SceneCarry(
        scene_id="003",
        objects=[
            CarryObject(name="title", kind="Text", position=(0.0, 3.2, 0.0)),
            CarryObject(name="formula", kind="MathTex", position=(0.0, 0.4, 0.0)),
        ],
    )
    path = write_carry(tmp_path, original)
    assert path.exists()
    restored = read_carry(tmp_path, "003")
    assert restored == original


def test_malformed_file_treated_as_empty(tmp_path: Path):
    path = carry_path(tmp_path, "004")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    carry = read_carry(tmp_path, "004")
    assert carry.objects == []


def test_empty_carry_helper():
    c = empty_carry("007")
    assert c.scene_id == "007"
    assert c.objects == []
