# Email Backorder List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mirror the per-match ntfy backorder list to an email, with a prefetch-safe clickable per-row backorder link.

**Architecture:** `check.py` (runs in GitHub Actions) sends one digest email per run listing non-auto matches, each row linking to a new `GET /confirm` page on the backorder server. That page is a no-op render; an explicit button there does the real `POST /backorder` (HMAC + cap + dedup unchanged). Email goes out via Gmail SMTP.

**Tech Stack:** Python 3.12, FastAPI, smtplib/email (stdlib), pytest, requests.

---

## File Structure

- Create: `email_notify.py` — thin Gmail-SMTP sender (mirrors `notify.py`).
- Modify: `check.py` — add `build_confirm_url`, `build_email_digest`, `notify_email`; wire into `main()`.
- Modify: `backorder_api.py` — add `GET /confirm` HTML page.
- Modify: `config.json` — add `notify.email` + `backorder.confirm_url`.
- Modify: `.github/workflows/check.yml` — pass `GMAIL_APP_PASSWORD` + `BACKORDER_HMAC_SECRET` to the `check.py` step.
- Create: `tests/test_email_notify.py`, `tests/test_email_digest.py`, `tests/test_confirm_endpoint.py`.

---

## Task 1: `email_notify.py` — Gmail SMTP sender

**Files:**
- Create: `email_notify.py`
- Test: `tests/test_email_notify.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_email_notify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'email_notify'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Gmail SMTP helper, used by check.py to email the backorder match list.

Single responsibility: send one multipart (text + HTML) email through Gmail's
submission server. Kept separate from the wordfreq-heavy check module, like
notify.py. A send failure is logged and swallowed so it never aborts a run.
"""
from __future__ import annotations

import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def email_send(*, to: str, sender: str, subject: str, html: str, text: str) -> None:
    try:
        password = os.environ.get("GMAIL_APP_PASSWORD", "")
        if not password:
            print("email send SKIPPED: GMAIL_APP_PASSWORD not set", file=sys.stderr)
            return
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.sendmail(sender, [to], msg.as_string())
    except Exception as e:
        print(f"email send FAILED: {e}", file=sys.stderr)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_email_notify.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add email_notify.py tests/test_email_notify.py
git commit -m "feat: add Gmail SMTP email helper"
```

---

## Task 2: `check.py` — `build_confirm_url` (signed /confirm link)

**Files:**
- Modify: `check.py` (near `build_action_url`, around line 214-233)
- Test: `tests/test_email_digest.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_email_digest.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_confirm_url'`.

- [ ] **Step 3: Write minimal implementation**

Replace the existing `build_action_url` body (lines ~214-233) with a shared signer that both functions delegate to. New code:

```python
def _build_signed_url(
    domain: str, base_url: str, ttl_hours: int, now: int | None
) -> str:
    """Append an HMAC-signed ?domain&exp&sig query to base_url.

    Empty base_url or missing secret returns "" - callers fall back to a plain
    notification with no action link.
    """
    if not base_url:
        return ""
    secret = os.environ.get("BACKORDER_HMAC_SECRET", "")
    if not secret:
        return ""
    exp = (now if now is not None else int(time.time())) + ttl_hours * 3600
    sig = hmac.new(
        secret.encode("utf-8"),
        f"{domain}|{exp}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"{base_url}?domain={domain}&exp={exp}&sig={sig}"


def build_action_url(
    domain: str, *, tunnel_url: str, ttl_hours: int, now: int | None = None
) -> str:
    """Construct the HMAC-signed ntfy Backorder action URL (POST target)."""
    return _build_signed_url(domain, tunnel_url, ttl_hours, now)


def build_confirm_url(
    domain: str, *, confirm_url: str, ttl_hours: int, now: int | None = None
) -> str:
    """Construct the HMAC-signed confirmation-page URL for the email link."""
    return _build_signed_url(domain, confirm_url, ttl_hours, now)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_email_digest.py tests/test_check_notification.py -v`
Expected: PASS (new confirm tests + existing build_action_url tests still green).

- [ ] **Step 5: Commit**

```bash
git add check.py tests/test_email_digest.py
git commit -m "refactor: share URL signer, add build_confirm_url"
```

---

## Task 3: `check.py` — `build_email_digest`

**Files:**
- Modify: `check.py` (add after `build_confirm_url`)
- Test: `tests/test_email_digest.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_email_digest.py`)

```python
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
    assert "Lefoglal" in html  # link label
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_email_digest.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_email_digest'`.

- [ ] **Step 3: Write minimal implementation** (add to `check.py`)

```python
def build_email_digest(
    matches: list[tuple[str, list[str]]],
    *,
    confirm_url: str,
    ttl_hours: int,
    now: int | None = None,
) -> tuple[str, str, str]:
    """Build (subject, text, html) for the per-run backorder digest email.

    matches: already filtered (auto-backorder domains excluded). Each entry is
    (domain, reasons). When confirm_url + secret are available, each row gets a
    signed "Lefoglalás" link to the /confirm page; otherwise a plain list.
    """
    subject = f"Domain watch - {len(matches)} talalat"

    text_rows: list[str] = []
    html_rows: list[str] = []
    for domain, reasons in matches:
        reason_summary = ", ".join(reasons[:2])
        link = build_confirm_url(
            domain, confirm_url=confirm_url, ttl_hours=ttl_hours, now=now
        )
        text_rows.append(f"{domain} - {reason_summary}"
                         + (f"  ->  {link}" if link else ""))
        if link:
            html_rows.append(
                f'<li><strong>{domain}</strong> - {reason_summary} '
                f'&nbsp; <a href="{link}">Lefoglalas &rarr;</a></li>'
            )
        else:
            html_rows.append(f"<li><strong>{domain}</strong> - {reason_summary}</li>")

    text = "\n".join(text_rows)
    html = "<html><body><ul>" + "".join(html_rows) + "</ul></body></html>"
    return subject, text, html
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_email_digest.py -v`
Expected: PASS (5 passed total in file).

- [ ] **Step 5: Commit**

```bash
git add check.py tests/test_email_digest.py
git commit -m "feat: build_email_digest for backorder match email"
```

---

## Task 4: `check.py` — `notify_email` + wire into `main()`

**Files:**
- Modify: `check.py` (add `notify_email`; refactor the `if matches:` block in `main()`, lines ~323-338)
- Test: `tests/test_email_digest.py` (append)

- [ ] **Step 1: Write the failing tests** (append)

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_email_digest.py -k notify_email -v`
Expected: FAIL — `AttributeError: module 'check' has no attribute 'email_notify'` (and no `notify_email`).

- [ ] **Step 3: Write minimal implementation**

Add the import near the top of `check.py` (with the other local imports, alongside `from notify import ...` at line 29):

```python
import email_notify
```

Add the function (after `build_email_digest`):

```python
def notify_email(
    matches: list[tuple[str, list[str]]], cfg: dict, now: int | None = None
) -> None:
    """Send the per-run digest email when enabled and there are matches."""
    email_cfg = cfg.get("notify", {}).get("email", {})
    if not email_cfg.get("enabled") or not matches:
        return
    bo = cfg.get("backorder", {})
    subject, text, html = build_email_digest(
        matches,
        confirm_url=bo.get("confirm_url", ""),
        ttl_hours=bo.get("action_ttl_hours", 24),
        now=now,
    )
    email_notify.email_send(
        to=email_cfg["to"],
        sender=email_cfg["from"],
        subject=subject,
        html=html,
        text=text,
    )
```

Refactor the `if matches:` block in `main()` (currently lines ~323-338) so the non-auto matches feed both ntfy and email:

```python
    if matches:
        tunnel_url = cfg.get("backorder", {}).get("tunnel_url", "")
        ttl_hours = cfg.get("backorder", {}).get("action_ttl_hours", 24)
        auto_domains = set(cfg.get("auto_backorder_domains", []))
        notifiable = [
            (domain, reasons)
            for domain, _release, reasons in matches
            if domain not in auto_domains
        ]
        for domain, reasons in notifiable:
            reason_summary = ", ".join(reasons[:2])
            title = f"{domain} - {reason_summary}"
            print(title)
            action_url = build_action_url(
                domain, tunnel_url=tunnel_url, ttl_hours=ttl_hours
            )
            headers = build_ntfy_headers(title=title, action_url=action_url)
            ntfy_send(headers, "")
        notify_email(notifiable, cfg)
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS (all existing tests + the new ones; no regressions in `test_check_notification.py`).

- [ ] **Step 5: Commit**

```bash
git add check.py tests/test_email_digest.py
git commit -m "feat: email the backorder match list per run"
```

---

## Task 5: `backorder_api.py` — `GET /confirm` page

**Files:**
- Modify: `backorder_api.py` (add route inside `create_app`, after the `/backorder` route; add `HTMLResponse` import)
- Test: `tests/test_confirm_endpoint.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the GET /confirm prefetch-safe confirmation page."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backorder_api
from backorder_api import create_app

SECRET = "testsecret"


def sign(domain: str, exp: int) -> str:
    msg = f"{domain}|{exp}".encode("utf-8")
    return hmac.new(SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:32]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BACKORDER_HMAC_SECRET", SECRET)


def write_cfg(tmp_path: Path, cfg: dict) -> Path:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def test_confirm_get_renders_post_form_and_does_not_book(tmp_path, cfg, monkeypatch):
    cfg_path = write_cfg(tmp_path, cfg)
    booked = []
    monkeypatch.setattr(
        backorder_api, "register_backorder",
        lambda *a, **k: booked.append(a) or None,
    )
    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)

    exp = int(time.time()) + 3600
    r = client.get(
        "/confirm",
        params={"domain": "foo.hu", "exp": exp, "sig": sign("foo.hu", exp)},
    )
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert 'method="post"' in body.lower()
    assert f"/backorder?domain=foo.hu&exp={exp}&sig={sign('foo.hu', exp)}" in body
    assert "foo.hu" in body
    # GET must NOT trigger a booking
    assert booked == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_confirm_endpoint.py -v`
Expected: FAIL — 404 (route not defined).

- [ ] **Step 3: Write minimal implementation**

Add the import at the top of `backorder_api.py` (with the FastAPI import, line ~22):

```python
from fastapi.responses import HTMLResponse
```

Add the route inside `create_app`, immediately after the `/backorder` route (after line ~94, before `return app`):

```python
    @app.get("/confirm", response_class=HTMLResponse)
    def confirm(
        domain: str = Query(..., min_length=4, max_length=63),
        exp: int = Query(...),
        sig: str = Query(..., min_length=32, max_length=32),
    ):
        # Render-only: prefetch-safe. The real booking is the POST below.
        action = f"/backorder?domain={domain}&exp={exp}&sig={sig}"
        return (
            "<!doctype html><html lang='hu'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Lefoglalas: {domain}</title></head><body>"
            f"<h2>{domain}</h2>"
            "<p>Megerosited a backorder leadasat?</p>"
            f"<form method='post' action='{action}'>"
            "<button type='submit'>Megerositem a foglalast</button>"
            "</form></body></html>"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_confirm_endpoint.py tests/test_backorder_api.py -v`
Expected: PASS (new confirm test + existing backorder tests).

- [ ] **Step 5: Commit**

```bash
git add backorder_api.py tests/test_confirm_endpoint.py
git commit -m "feat: GET /confirm prefetch-safe backorder confirmation page"
```

---

## Task 6: Config + workflow wiring (non-TDD)

**Files:**
- Modify: `config.json`
- Modify: `.github/workflows/check.yml`

- [ ] **Step 1: Add config keys**

In `config.json`, add a top-level `notify` block and a `confirm_url` to `backorder`:

```json
  "notify": {
    "email": {
      "enabled": true,
      "to": "parapet@freestart.hu",
      "from": "jollylama06@gmail.com"
    }
  },
  "backorder": {
    "enabled": true,
    "dry_run": false,
    "daily_cap": 3,
    "tunnel_url": "https://backorder.babakocsiszakaruhaz.hu/backorder",
    "confirm_url": "https://backorder.babakocsiszakaruhaz.hu/confirm",
    "action_ttl_hours": 24
  },
```

- [ ] **Step 2: Verify JSON is valid**

Run: `python -c "import json; json.load(open('config.json', encoding='utf-8-sig')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Pass the secrets to check.py in the workflow**

In `.github/workflows/check.yml`, extend the `python check.py` step env (lines ~36-39):

```yaml
      - run: python check.py
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          BACKORDER_HMAC_SECRET: ${{ secrets.BACKORDER_HMAC_SECRET }}
          GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
```

- [ ] **Step 4: Commit**

```bash
git add config.json .github/workflows/check.yml
git commit -m "chore: config + workflow env for email backorder list"
```

- [ ] **Step 5: Manual setup (user action — outside the repo)**

Add two GitHub Actions repository secrets (Settings -> Secrets and variables -> Actions):
- `GMAIL_APP_PASSWORD` — a Google App Password for `jollylama06@gmail.com` (Google Account -> Security -> 2-Step Verification -> App passwords).
- `BACKORDER_HMAC_SECRET` — the same secret value the backorder server uses (must match, or links fail signature check). If it is already a secret, confirm the value matches the server's.

---

## Final verification

- [ ] Run the full suite: `python -m pytest -q` — all green.
- [ ] Sanity-check the email locally (optional, requires a real app password in env):
  `python -c "import email_notify; email_notify.email_send(to='parapet@freestart.hu', sender='jollylama06@gmail.com', subject='test', html='<p>hi</p>', text='hi')"`

---

## Self-Review notes

- **Spec coverage:** confirm endpoint (Task 5), email sender (Task 1), digest in check.py (Tasks 2-4), config+secrets+workflow incl. BACKORDER_HMAC_SECRET wiring (Task 6), tests for all three areas. Per-row links only, no "book all" — matches spec. Recipient `parapet@freestart.hu` — matches user decision.
- **Placeholder scan:** none — every code/command step is concrete.
- **Type consistency:** `build_email_digest` returns `(subject, text, html)` and `email_send(*, to, sender, subject, html, text)` is called with exactly those kwargs in `notify_email`; `build_confirm_url`/`build_action_url` both delegate to `_build_signed_url`; matches list is `list[tuple[str, list[str]]]` throughout.
- **Note:** the confirm form posts to a **relative** `/backorder?...` URL, so the page is host-agnostic; the absolute host lives only in the email's `confirm_url`.
