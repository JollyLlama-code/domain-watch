"""Tests for check.run_ip_guard wiring."""
from __future__ import annotations

import check


def test_run_ip_guard_alerts_and_saves_on_change(monkeypatch):
    sent = {}
    saved = {}
    monkeypatch.setattr(check.ip_guard, "current_public_ip", lambda: "5.6.7.8")
    monkeypatch.setattr(check.ip_guard, "load_known_ip", lambda: "1.2.3.4")
    monkeypatch.setattr(
        check.ip_guard, "save_known_ip",
        lambda ip, ts: saved.update(ip=ip, ts=ts),
    )
    monkeypatch.setattr(
        check, "ntfy_send",
        lambda headers, body="": sent.update(headers=headers, body=body),
    )

    check.run_ip_guard()

    assert "valtozott" in sent["headers"]["Title"]
    assert "5.6.7.8" in sent["body"]
    assert saved["ip"] == "5.6.7.8"


def test_run_ip_guard_silent_when_unchanged(monkeypatch):
    sent = {}
    monkeypatch.setattr(check.ip_guard, "current_public_ip", lambda: "1.2.3.4")
    monkeypatch.setattr(check.ip_guard, "load_known_ip", lambda: "1.2.3.4")
    monkeypatch.setattr(check.ip_guard, "save_known_ip", lambda *a: sent.update(saved=True))
    monkeypatch.setattr(check, "ntfy_send", lambda *a, **k: sent.update(called=True))

    check.run_ip_guard()

    assert sent == {}


def test_run_ip_guard_silent_when_lookup_fails(monkeypatch):
    sent = {}
    monkeypatch.setattr(check.ip_guard, "current_public_ip", lambda: None)
    monkeypatch.setattr(check.ip_guard, "load_known_ip", lambda: "1.2.3.4")
    monkeypatch.setattr(check, "ntfy_send", lambda *a, **k: sent.update(called=True))

    check.run_ip_guard()

    assert sent == {}
