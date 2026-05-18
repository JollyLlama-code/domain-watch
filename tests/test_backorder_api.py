"""Tests for the FastAPI backorder endpoint."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backorder_api import create_app


SECRET = "testsecret"


def sign(domain: str, exp: int) -> str:
    msg = f"{domain}|{exp}".encode("utf-8")
    return hmac.new(SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:32]


def write_cfg(tmp_path: Path, cfg: dict) -> Path:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MICROWARE_API_PASSWORD", "x")
    monkeypatch.setenv("BACKORDER_HMAC_SECRET", SECRET)


def test_valid_dry_run_request_returns_200(tmp_path, cfg):
    cfg["backorder"]["dry_run"] = True
    cfg_path = write_cfg(tmp_path, cfg)
    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)

    exp = int(time.time()) + 3600
    r = client.post(
        "/backorder",
        params={"domain": "foo.hu", "exp": exp, "sig": sign("foo.hu", exp)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["mode"] == "dry_run"


def test_bad_signature_returns_403(tmp_path, cfg):
    cfg_path = write_cfg(tmp_path, cfg)
    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)

    exp = int(time.time()) + 3600
    r = client.post(
        "/backorder",
        params={"domain": "foo.hu", "exp": exp, "sig": "0" * 32},
    )
    assert r.status_code == 403


def test_expired_signature_returns_403(tmp_path, cfg):
    cfg_path = write_cfg(tmp_path, cfg)
    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)

    exp = int(time.time()) - 60
    r = client.post(
        "/backorder",
        params={"domain": "foo.hu", "exp": exp, "sig": sign("foo.hu", exp)},
    )
    assert r.status_code == 403


def test_disabled_flag_returns_503(tmp_path, cfg):
    cfg["backorder"]["enabled"] = False
    cfg_path = write_cfg(tmp_path, cfg)
    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)

    exp = int(time.time()) + 3600
    r = client.post(
        "/backorder",
        params={"domain": "foo.hu", "exp": exp, "sig": sign("foo.hu", exp)},
    )
    assert r.status_code == 503


def test_cap_reached_returns_429(tmp_path, cfg):
    cfg["backorder"]["dry_run"] = True
    cfg["backorder"]["daily_cap"] = 1
    cfg_path = write_cfg(tmp_path, cfg)
    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)

    exp = int(time.time()) + 3600
    r1 = client.post(
        "/backorder",
        params={"domain": "a.hu", "exp": exp, "sig": sign("a.hu", exp)},
    )
    assert r1.status_code == 200
    r2 = client.post(
        "/backorder",
        params={"domain": "b.hu", "exp": exp, "sig": sign("b.hu", exp)},
    )
    assert r2.status_code == 429


def test_resubmit_same_domain_returns_200_duplicate(tmp_path, cfg):
    cfg["backorder"]["dry_run"] = True
    cfg_path = write_cfg(tmp_path, cfg)
    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)

    exp = int(time.time()) + 3600
    p = {"domain": "foo.hu", "exp": exp, "sig": sign("foo.hu", exp)}
    r1 = client.post("/backorder", params=p)
    r2 = client.post("/backorder", params=p)
    assert r1.status_code == 200 and r1.json()["mode"] == "dry_run"
    assert r2.status_code == 200 and r2.json()["mode"] == "duplicate"
