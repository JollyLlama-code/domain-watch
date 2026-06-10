"""Tests for the FastAPI backorder endpoint."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backorder_api
import backorder_runner
from backorder_api import create_app
from microware_client import RegisterResult


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


def test_expired_tap_notifies_user(tmp_path, cfg, monkeypatch):
    cfg_path = write_cfg(tmp_path, cfg)
    alerts = []
    monkeypatch.setattr(
        backorder_api, "tap_failure_push",
        lambda domain, kind: alerts.append((domain, kind)),
    )
    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)

    exp = int(time.time()) - 60
    r = client.post(
        "/backorder",
        params={"domain": "foo.hu", "exp": exp, "sig": sign("foo.hu", exp)},
    )
    assert r.status_code == 403
    assert alerts == [("foo.hu", "expired")]


def test_bad_signature_does_not_notify(tmp_path, cfg, monkeypatch):
    cfg_path = write_cfg(tmp_path, cfg)
    alerts = []
    monkeypatch.setattr(
        backorder_api, "tap_failure_push",
        lambda domain, kind: alerts.append((domain, kind)),
    )
    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)

    exp = int(time.time()) + 3600
    r = client.post(
        "/backorder",
        params={"domain": "foo.hu", "exp": exp, "sig": "0" * 32},
    )
    assert r.status_code == 403
    assert alerts == []


def test_cap_reached_notifies_user(tmp_path, cfg, monkeypatch):
    cfg["backorder"]["dry_run"] = True
    cfg["backorder"]["daily_cap"] = 1
    cfg_path = write_cfg(tmp_path, cfg)
    alerts = []
    monkeypatch.setattr(
        backorder_api, "tap_failure_push",
        lambda domain, kind: alerts.append((domain, kind)),
    )
    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)

    exp = int(time.time()) + 3600
    client.post(
        "/backorder",
        params={"domain": "a.hu", "exp": exp, "sig": sign("a.hu", exp)},
    )
    r2 = client.post(
        "/backorder",
        params={"domain": "b.hu", "exp": exp, "sig": sign("b.hu", exp)},
    )
    assert r2.status_code == 429
    assert alerts == [("b.hu", "cap")]


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


def test_10401_triggers_ntfy_alert(tmp_path, cfg, monkeypatch):
    cfg["backorder"]["dry_run"] = False
    cfg_path = write_cfg(tmp_path, cfg)

    monkeypatch.setattr(
        backorder_api, "register_backorder",
        lambda domain, c, **k: RegisterResult(
            success=False, mode="live", error_number=10401,
            api_message="10401: Authentication failed",
        ),
    )
    alerts = []
    monkeypatch.setattr(
        backorder_runner, "ntfy_send",
        lambda headers, body="": alerts.append((headers, body)),
    )
    monkeypatch.setattr(backorder_runner.ip_guard, "load_known_ip", lambda: "1.2.3.4")

    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)
    exp = int(time.time()) + 3600
    r = client.post(
        "/backorder",
        params={"domain": "foo.hu", "exp": exp, "sig": sign("foo.hu", exp)},
    )
    assert r.status_code == 200, r.text
    assert len(alerts) == 1
    assert "1.2.3.4" in alerts[0][1]
    assert "10401" in alerts[0][1]


def test_successful_live_result_pushes_and_logs(tmp_path, cfg, monkeypatch):
    cfg["backorder"]["dry_run"] = False
    cfg_path = write_cfg(tmp_path, cfg)

    monkeypatch.setattr(
        backorder_api, "register_backorder",
        lambda domain, c, **k: RegisterResult(
            success=True, mode="live", api_code=201, order_id=42,
        ),
    )
    alerts = []
    monkeypatch.setattr(
        backorder_runner, "ntfy_send",
        lambda headers, body="": alerts.append((headers, body)),
    )

    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)
    exp = int(time.time()) + 3600
    r = client.post(
        "/backorder",
        params={"domain": "bar.hu", "exp": exp, "sig": sign("bar.hu", exp)},
    )
    assert r.status_code == 200, r.text
    # success now sends a result push and writes the audit log
    assert len(alerts) == 1
    assert "ELKAPVA" in alerts[0][1]
    assert "42" in alerts[0][1]
    log_lines = (tmp_path / "backorder.log").read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 1
    entry = json.loads(log_lines[0])
    assert entry["domain"] == "bar.hu"
    assert entry["success"] is True
    assert entry["order_id"] == 42


def test_rejected_live_result_pushes_and_logs(tmp_path, cfg, monkeypatch):
    cfg["backorder"]["dry_run"] = False
    cfg_path = write_cfg(tmp_path, cfg)

    monkeypatch.setattr(
        backorder_api, "register_backorder",
        lambda domain, c, **k: RegisterResult(
            success=False, mode="live", http_status=200, api_code=400,
            api_message="domain not in pre-deletion",
        ),
    )
    alerts = []
    monkeypatch.setattr(
        backorder_runner, "ntfy_send",
        lambda headers, body="": alerts.append((headers, body)),
    )

    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)
    exp = int(time.time()) + 3600
    r = client.post(
        "/backorder",
        params={"domain": "bar.hu", "exp": exp, "sig": sign("bar.hu", exp)},
    )
    assert r.status_code == 200, r.text
    assert len(alerts) == 1
    assert "ELUTASITVA" in alerts[0][1]
    assert "domain not in pre-deletion" in alerts[0][1]
    entry = json.loads((tmp_path / "backorder.log").read_text(encoding="utf-8").strip())
    assert entry["success"] is False
    assert entry["api_code"] == 400
