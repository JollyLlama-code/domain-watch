"""Tests for check.py URL signing and per-match notification format."""
from __future__ import annotations

import hashlib
import hmac

from check import build_action_url, build_ntfy_headers


SECRET = "topsecret"


def _expected_sig(domain: str, exp: int) -> str:
    msg = f"{domain}|{exp}".encode("utf-8")
    return hmac.new(SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:32]


def test_build_action_url_has_signed_query(monkeypatch):
    monkeypatch.setenv("BACKORDER_HMAC_SECRET", SECRET)
    url = build_action_url(
        "foo.hu",
        tunnel_url="https://tun.example/backorder",
        ttl_hours=24,
        now=1_700_000_000,
    )
    expected_exp = 1_700_000_000 + 24 * 3600
    expected_sig = _expected_sig("foo.hu", expected_exp)
    assert "domain=foo.hu" in url
    assert f"exp={expected_exp}" in url
    assert f"sig={expected_sig}" in url
    assert url.startswith("https://tun.example/backorder?")


def test_build_action_url_empty_tunnel_returns_empty(monkeypatch):
    monkeypatch.setenv("BACKORDER_HMAC_SECRET", SECRET)
    assert build_action_url("foo.hu", tunnel_url="", ttl_hours=24) == ""


def test_build_action_url_missing_secret_returns_empty(monkeypatch):
    monkeypatch.delenv("BACKORDER_HMAC_SECRET", raising=False)
    assert (
        build_action_url("foo.hu", tunnel_url="https://x", ttl_hours=24) == ""
    )


def test_build_ntfy_headers_with_action(monkeypatch):
    monkeypatch.setenv("BACKORDER_HMAC_SECRET", SECRET)
    headers = build_ntfy_headers(
        title="foo.hu - en word",
        action_url="https://tun.example/backorder?domain=foo.hu&exp=1&sig=ab",
    )
    assert headers["Title"] == "foo.hu - en word"
    assert "Actions" in headers
    assert "Backorder" in headers["Actions"]
    assert "https://tun.example/backorder" in headers["Actions"]
    assert "clear=true" in headers["Actions"]


def test_build_ntfy_headers_without_action():
    headers = build_ntfy_headers(title="foo.hu - en word", action_url="")
    assert headers["Title"] == "foo.hu - en word"
    assert "Actions" not in headers
