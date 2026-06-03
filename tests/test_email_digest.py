"""Tests for check.py email digest building and confirm-link signing."""
from __future__ import annotations

import hashlib
import hmac

from check import build_confirm_url

SECRET = "topsecret"


def _expected_sig(domain: str, exp: int) -> str:
    msg = f"{domain}|{exp}".encode("utf-8")
    return hmac.new(SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:32]


def test_build_confirm_url_signs_against_confirm_base(monkeypatch):
    monkeypatch.setenv("BACKORDER_HMAC_SECRET", SECRET)
    url = build_confirm_url(
        "foo.hu",
        confirm_url="https://tun.example/confirm",
        ttl_hours=24,
        now=1_700_000_000,
    )
    exp = 1_700_000_000 + 24 * 3600
    assert url.startswith("https://tun.example/confirm?")
    assert "domain=foo.hu" in url
    assert f"exp={exp}" in url
    assert f"sig={_expected_sig('foo.hu', exp)}" in url


def test_build_confirm_url_empty_base_returns_empty(monkeypatch):
    monkeypatch.setenv("BACKORDER_HMAC_SECRET", SECRET)
    assert build_confirm_url("foo.hu", confirm_url="", ttl_hours=24) == ""


def test_build_confirm_url_missing_secret_returns_empty(monkeypatch):
    monkeypatch.delenv("BACKORDER_HMAC_SECRET", raising=False)
    assert build_confirm_url("foo.hu", confirm_url="https://x", ttl_hours=24) == ""


from check import build_email_digest


def test_digest_lists_each_match_with_confirm_link(monkeypatch):
    monkeypatch.setenv("BACKORDER_HMAC_SECRET", SECRET)
    matches = [("foo.hu", ["en word"]), ("bar.hu", ["short", "all-numeric"])]
    subject, text, html = build_email_digest(
        matches,
        confirm_url="https://tun.example/confirm",
        ttl_hours=24,
        now=1_700_000_000,
    )
    assert "2" in subject
    # both domains present in both parts
    for d in ("foo.hu", "bar.hu"):
        assert d in text and d in html
    # html has a confirm link per row, pointing at /confirm
    assert html.count("https://tun.example/confirm?domain=") == 2
    assert "Lefoglalas" in html  # link label
    # reason summary shown
    assert "en word" in html


def test_digest_without_link_when_no_confirm_url(monkeypatch):
    monkeypatch.setenv("BACKORDER_HMAC_SECRET", SECRET)
    subject, text, html = build_email_digest(
        [("foo.hu", ["en word"])],
        confirm_url="",
        ttl_hours=24,
        now=1_700_000_000,
    )
    assert "foo.hu" in html
    assert "/confirm?domain=" not in html  # no link rendered
