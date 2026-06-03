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
