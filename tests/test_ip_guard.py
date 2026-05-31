"""Tests for the public-IP watchdog."""
from __future__ import annotations

from pathlib import Path

import ip_guard


def test_evaluate_none_when_lookup_failed():
    assert ip_guard.evaluate(None, "1.2.3.4") is None


def test_evaluate_initial_when_no_known_ip():
    action, title, body = ip_guard.evaluate("1.2.3.4", None)
    assert action == "initial"
    assert "1.2.3.4" in body


def test_evaluate_changed_when_ip_differs():
    action, title, body = ip_guard.evaluate("5.6.7.8", "1.2.3.4")
    assert action == "changed"
    assert "1.2.3.4" in body and "5.6.7.8" in body


def test_evaluate_none_when_unchanged():
    assert ip_guard.evaluate("1.2.3.4", "1.2.3.4") is None


def test_save_and_load_round_trip(tmp_path: Path):
    p = tmp_path / "whitelisted_ip.json"
    ip_guard.save_known_ip("9.9.9.9", "2026-05-31T00:00:00+00:00", path=p)
    assert ip_guard.load_known_ip(path=p) == "9.9.9.9"


def test_load_returns_none_when_missing(tmp_path: Path):
    assert ip_guard.load_known_ip(path=tmp_path / "nope.json") is None


def test_current_public_ip_success(monkeypatch):
    class FakeResp:
        text = "203.0.113.7\n"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(ip_guard.requests, "get", lambda *a, **k: FakeResp())
    assert ip_guard.current_public_ip() == "203.0.113.7"


def test_current_public_ip_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(ip_guard.requests, "get", boom)
    assert ip_guard.current_public_ip() is None
