"""Tests for the Gmail SMTP helper."""
from __future__ import annotations

import email_notify


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.started_tls = False
        self.logged_in = None
        self.sent = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def sendmail(self, from_addr, to_addrs, msg):
        self.sent = (from_addr, to_addrs, msg)


def test_email_send_uses_gmail_starttls_and_login(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-pw")
    monkeypatch.setattr(email_notify.smtplib, "SMTP", FakeSMTP)

    email_notify.email_send(
        to="parapet@freestart.hu",
        sender="jollylama06@gmail.com",
        subject="Domain watch",
        html="<p>foo.hu</p>",
        text="foo.hu",
    )

    s = FakeSMTP.instances[0]
    assert s.host == "smtp.gmail.com" and s.port == 587
    assert s.started_tls is True
    assert s.logged_in == ("jollylama06@gmail.com", "app-pw")
    from_addr, to_addrs, raw = s.sent
    assert from_addr == "jollylama06@gmail.com"
    assert to_addrs == ["parapet@freestart.hu"]
    assert "Domain watch" in raw
    assert "foo.hu" in raw


def test_email_send_swallows_errors(monkeypatch):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-pw")

    def boom(*a, **k):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(email_notify.smtplib, "SMTP", boom)
    # must not raise
    email_notify.email_send(
        to="x@y.hu", sender="a@gmail.com", subject="s", html="<p>h</p>", text="h"
    )
