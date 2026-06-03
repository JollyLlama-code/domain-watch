"""Tests for check.py email digest building and confirm-link signing."""
from __future__ import annotations

import hashlib
import hmac

from check import build_confirm_url, build_email_digest

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


def test_digest_lists_each_match_with_confirm_link(monkeypatch):
    monkeypatch.setenv("BACKORDER_HMAC_SECRET", SECRET)
    matches = [
        ("foo.hu", ["en word"]),
        ("bar.hu", ["short", "all-numeric"]),
        ("baz.hu", ["short", "en word", "all-numeric"]),
    ]
    subject, text, html = build_email_digest(
        matches,
        confirm_url="https://tun.example/confirm",
        ttl_hours=24,
        now=1_700_000_000,
    )
    assert subject == "Domain watch - 3 talalat"
    # all three domains present in both parts
    for d in ("foo.hu", "bar.hu", "baz.hu"):
        assert d in text and d in html
    # html has a confirm link per row, pointing at /confirm
    assert html.count("https://tun.example/confirm?domain=") == 3
    assert "Lefoglalas" in html  # link label
    # reason summary shown
    assert "en word" in html
    # baz.hu: first two reasons shown, third truncated
    assert "short, en word" in html      # baz: first two reasons joined
    assert "short, en word, all-numeric" not in html  # third reason truncated


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


import check


def _cfg(email_enabled=True):
    return {
        "notify": {"email": {"enabled": email_enabled,
                              "to": "parapet@freestart.hu",
                              "from": "jollylama06@gmail.com"}},
        "backorder": {"confirm_url": "https://tun.example/confirm",
                      "action_ttl_hours": 24},
    }


def test_notify_email_sends_one_email_with_all_matches(monkeypatch):
    monkeypatch.setenv("BACKORDER_HMAC_SECRET", SECRET)
    sent = []
    monkeypatch.setattr(
        check.email_notify, "email_send",
        lambda **kw: sent.append(kw),
    )
    check.notify_email(
        [("foo.hu", ["en word"]), ("bar.hu", ["short"])],
        _cfg(), now=1_700_000_000,
    )
    assert len(sent) == 1
    kw = sent[0]
    assert kw["to"] == "parapet@freestart.hu"
    assert kw["sender"] == "jollylama06@gmail.com"
    assert "foo.hu" in kw["html"] and "bar.hu" in kw["html"]


def test_notify_email_skipped_when_disabled(monkeypatch):
    sent = []
    monkeypatch.setattr(check.email_notify, "email_send", lambda **kw: sent.append(kw))
    check.notify_email([("foo.hu", ["en word"])], _cfg(email_enabled=False))
    assert sent == []


def test_notify_email_skipped_when_no_matches(monkeypatch):
    sent = []
    monkeypatch.setattr(check.email_notify, "email_send", lambda **kw: sent.append(kw))
    check.notify_email([], _cfg())
    assert sent == []
