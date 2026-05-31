"""Tests for the shared ntfy helper."""
from __future__ import annotations

import notify


def test_ntfy_send_posts_to_topic(monkeypatch):
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        return FakeResp()

    monkeypatch.setattr(notify.requests, "post", fake_post)
    notify.ntfy_send({"Title": "hi"}, "body text")

    assert captured["url"].startswith("https://ntfy.sh/")
    assert notify.NTFY_TOPIC in captured["url"]
    assert captured["headers"] == {"Title": "hi"}
    assert captured["data"] == b"body text"


def test_ntfy_send_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(notify.requests, "post", boom)
    notify.ntfy_send({"Title": "hi"}, "")  # must not raise
