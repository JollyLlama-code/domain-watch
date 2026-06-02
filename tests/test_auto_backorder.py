"""Tests for the automatic watched-domain backorder path."""
from __future__ import annotations

import json

import pytest

from auto_backorder import load_placed, mark_placed


def test_load_placed_missing_file_returns_empty(tmp_path):
    assert load_placed(tmp_path / "auto_backorder_state.json") == {}


def test_load_placed_corrupt_file_returns_empty(tmp_path):
    p = tmp_path / "auto_backorder_state.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_placed(p) == {}


def test_mark_placed_then_load_roundtrip(tmp_path):
    p = tmp_path / "auto_backorder_state.json"
    mark_placed(p, "babakocsi.hu", 184517)
    placed = load_placed(p)
    assert "babakocsi.hu" in placed
    assert placed["babakocsi.hu"]["orderid"] == 184517
    assert "ts" in placed["babakocsi.hu"]


def test_mark_placed_preserves_existing_entries(tmp_path):
    p = tmp_path / "auto_backorder_state.json"
    mark_placed(p, "a.hu", 1)
    mark_placed(p, "b.hu", 2)
    placed = load_placed(p)
    assert placed["a.hu"]["orderid"] == 1
    assert placed["b.hu"]["orderid"] == 2
