"""Tests for the automatic watched-domain backorder path."""
from __future__ import annotations

import json

import pytest
import responses
import backorder_runner

from auto_backorder import load_placed, mark_placed, run_auto_backorders


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


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MICROWARE_API_PASSWORD", "x")


@pytest.fixture(autouse=True)
def _silence_ntfy(monkeypatch):
    monkeypatch.setattr(backorder_runner, "ntfy_send", lambda headers, body="": None)


def _rows(*domains):
    return [(d, "2026-06-02", "2026-07-03") for d in domains]


def _add_register(status, json_body):
    responses.add(
        responses.POST, "https://api.microware.hu/domains/register",
        json=json_body, status=status,
    )


@responses.activate
def test_run_submits_present_unplaced_domain(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = False
    _add_register(201, {"domain": {"orderid": 77}, "result": {"code": 201, "message": "Created"}})

    run_auto_backorders(cfg, _rows("babakocsi.hu", "other.hu"), tmp_path)

    placed = load_placed(tmp_path / "auto_backorder_state.json")
    assert placed["babakocsi.hu"]["orderid"] == 77
    assert "babakocsi.hu.backorder" in responses.calls[0].request.body
    assert (tmp_path / "backorder.log").exists()


@responses.activate
def test_run_skips_already_placed(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = False
    mark_placed(tmp_path / "auto_backorder_state.json", "babakocsi.hu", 1)
    _add_register(201, {"domain": {"orderid": 77}, "result": {"code": 201}})

    run_auto_backorders(cfg, _rows("babakocsi.hu"), tmp_path)

    assert len(responses.calls) == 0


@responses.activate
def test_run_skips_domain_not_on_list(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = False
    _add_register(201, {"domain": {"orderid": 77}, "result": {"code": 201}})

    run_auto_backorders(cfg, _rows("unrelated.hu"), tmp_path)

    assert len(responses.calls) == 0
    assert load_placed(tmp_path / "auto_backorder_state.json") == {}


def test_run_dry_run_does_not_mark_placed(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = True

    run_auto_backorders(cfg, _rows("babakocsi.hu"), tmp_path)

    assert load_placed(tmp_path / "auto_backorder_state.json") == {}
    assert (tmp_path / "dry_run.log").exists()


@responses.activate
def test_run_failure_does_not_mark_placed(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = False
    _add_register(400, {"result": {"code": 400, "message": "10256: not available"}})

    run_auto_backorders(cfg, _rows("babakocsi.hu"), tmp_path)

    assert load_placed(tmp_path / "auto_backorder_state.json") == {}
    assert (tmp_path / "backorder.log").exists()


@responses.activate
def test_run_bypasses_daily_cap_state(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = False
    _add_register(201, {"domain": {"orderid": 77}, "result": {"code": 201}})

    run_auto_backorders(cfg, _rows("babakocsi.hu"), tmp_path)

    assert not (tmp_path / "daily_count.json").exists()
    assert not (tmp_path / "submitted_today.json").exists()


@responses.activate
def test_run_noop_without_watched_domains(tmp_path, cfg):
    cfg.pop("auto_backorder_domains", None)
    _add_register(201, {"domain": {"orderid": 77}, "result": {"code": 201}})

    run_auto_backorders(cfg, _rows("babakocsi.hu"), tmp_path)

    assert len(responses.calls) == 0
