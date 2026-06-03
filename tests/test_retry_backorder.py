"""Tests for the standalone per-minute retry entry point."""
from __future__ import annotations

import retry_backorder


def test_main_attempts_each_watched_domain(tmp_path, monkeypatch):
    cfg = {"auto_backorder_domains": ["a.hu", "b.hu"], "backorder": {"dry_run": True}}
    monkeypatch.setattr(retry_backorder, "load_config", lambda: cfg)
    monkeypatch.setattr(retry_backorder, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(retry_backorder, "attempt_watched_now", lambda c, sd, d: calls.append(d))

    rc = retry_backorder.main()

    assert rc == 0
    assert calls == ["a.hu", "b.hu"]


def test_main_noop_without_watched_domains(tmp_path, monkeypatch):
    monkeypatch.setattr(retry_backorder, "load_config", lambda: {"backorder": {"dry_run": True}})
    monkeypatch.setattr(retry_backorder, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(retry_backorder, "attempt_watched_now", lambda c, sd, d: calls.append(d))

    assert retry_backorder.main() == 0
    assert calls == []
