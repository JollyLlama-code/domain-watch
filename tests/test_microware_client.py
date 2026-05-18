"""Tests for microware_client."""
from __future__ import annotations

import pytest
import responses

from microware_client import (
    HU_DECLARATION_TEXT,
    build_register_body,
    register_backorder,
)


@pytest.fixture(autouse=True)
def _set_api_password(monkeypatch):
    monkeypatch.setenv("MICROWARE_API_PASSWORD", "testpass")


def test_build_register_body_includes_required_hu_fields(cfg):
    body = build_register_body("foo.hu", cfg)
    assert body["domain"] == "foo.hu"
    assert body["years"] == 2
    assert body["ns1"] == "ns1.microware.hu"
    assert body["ns2"] == "ns2.microware.hu"
    assert body["owner"] == "12345"
    assert body["type"] == "1f"
    assert body["declarations"] == HU_DECLARATION_TEXT


def test_hu_declaration_text_has_required_phrases():
    assert "Domainregisztrációs Szabályzat" in HU_DECLARATION_TEXT
    assert "Alternatív Vitarendező Fórum" in HU_DECLARATION_TEXT


@responses.activate
def test_register_backorder_success(cfg):
    responses.add(
        responses.POST,
        "https://api.microware.hu/domains/register",
        json={
            "domain": {"orderid": 42, "domainids": "99", "invoiceid": 7},
            "result": {"code": 201, "message": "Created"},
        },
        status=201,
    )
    res = register_backorder("foo.hu", cfg, dry_run=False)
    assert res.success is True
    assert res.mode == "live"
    assert res.order_id == 42
    assert res.api_code == 201


@responses.activate
def test_register_backorder_lost_catch_returns_error_number(cfg):
    responses.add(
        responses.POST,
        "https://api.microware.hu/domains/register",
        json={
            "domain": {},
            "result": {"code": 400, "message": "10362: Failed backorder registration"},
        },
        status=400,
    )
    res = register_backorder("foo.hu", cfg, dry_run=False)
    assert res.success is False
    assert res.error_number == 10362
    assert res.api_code == 400


def test_register_backorder_dry_run_writes_log_and_skips_http(cfg, tmp_path):
    log = tmp_path / "dry_run.log"
    res = register_backorder("bar.hu", cfg, dry_run=True, log_path=str(log))
    assert res.success is True
    assert res.mode == "dry_run"
    assert res.request_body["domain"] == "bar.hu"
    contents = log.read_text(encoding="utf-8")
    assert "bar.hu" in contents
