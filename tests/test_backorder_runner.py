"""Tests for the shared backorder log + push helpers."""
from __future__ import annotations

import json
from datetime import datetime

import backorder_runner
from backorder_runner import log_backorder, result_push, tap_failure_push
from microware_client import RegisterResult


def test_result_push_prefix_tags_title_and_body(monkeypatch):
    sent = []
    monkeypatch.setattr(backorder_runner, "ntfy_send",
                        lambda headers, body="": sent.append((headers, body)))
    result = RegisterResult(success=True, mode="live", api_code=201, order_id=7)
    result_push("foo.hu", result, prefix="AUTO")
    headers, body = sent[0]
    assert headers["Title"] == "AUTO foo.hu - ELKAPVA"
    assert headers["Tags"] == "white_check_mark"
    assert headers["Priority"] == "high"
    assert body.startswith("AUTO foo.hu: ELKAPVA")
    assert "order 7" in body


def test_result_push_no_prefix_unchanged(monkeypatch):
    sent = []
    monkeypatch.setattr(backorder_runner, "ntfy_send",
                        lambda headers, body="": sent.append((headers, body)))
    result = RegisterResult(success=True, mode="live", api_code=201, order_id=7)
    result_push("foo.hu", result)
    headers, _ = sent[0]
    assert headers["Title"] == "foo.hu - ELKAPVA"


def test_tap_failure_push_expired_names_domain(monkeypatch):
    sent = []
    monkeypatch.setattr(backorder_runner, "ntfy_send",
                        lambda headers, body="": sent.append((headers, body)))
    tap_failure_push("foo.hu", "expired")
    headers, body = sent[0]
    assert "LEJART" in headers["Title"]
    assert "foo.hu" in headers["Title"]
    assert "foo.hu" in body


def test_tap_failure_push_cap_names_limit(monkeypatch):
    sent = []
    monkeypatch.setattr(backorder_runner, "ntfy_send",
                        lambda headers, body="": sent.append((headers, body)))
    tap_failure_push("bar.hu", "cap")
    headers, body = sent[0]
    assert "LIMIT" in headers["Title"]
    assert "bar.hu" in headers["Title"]


def test_log_backorder_writes_one_json_line(tmp_path):
    result = RegisterResult(success=True, mode="live", api_code=201, order_id=7)
    log_backorder(tmp_path, "foo.hu", result)
    lines = (tmp_path / "backorder.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["domain"] == "foo.hu"
    assert entry["order_id"] == 7
    # ts is the primary audit field — confirm it is a valid ISO timestamp
    assert datetime.fromisoformat(entry["ts"])
