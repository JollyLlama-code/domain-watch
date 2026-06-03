"""Tests for the automatic watched-domain backorder path."""
from __future__ import annotations

import json

import pytest
import responses
import backorder_runner

import check
import auto_backorder
from auto_backorder import (
    attempt_watched_now,
    load_placed,
    mark_placed,
    run_auto_backorders,
)
from microware_client import RegisterResult


def test_load_placed_missing_file_returns_empty(tmp_path):
    assert load_placed(tmp_path / "auto_backorder_state.json") == {}


def test_load_placed_corrupt_file_returns_empty(tmp_path):
    p = tmp_path / "auto_backorder_state.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_placed(p) == {}


def test_mark_placed_then_load_roundtrip(tmp_path):
    p = tmp_path / "auto_backorder_state.json"
    mark_placed(p, "babakocsi.hu", 184517)
    placed = load_placed(p)
    assert "babakocsi.hu" in placed
    assert placed["babakocsi.hu"]["orderid"] == 184517
    assert "ts" in placed["babakocsi.hu"]


def test_mark_placed_preserves_existing_entries(tmp_path):
    p = tmp_path / "auto_backorder_state.json"
    mark_placed(p, "a.hu", 1)
    mark_placed(p, "b.hu", 2)
    placed = load_placed(p)
    assert placed["a.hu"]["orderid"] == 1
    assert placed["b.hu"]["orderid"] == 2


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MICROWARE_API_PASSWORD", "x")


@pytest.fixture(autouse=True)
def _silence_ntfy(monkeypatch):
    monkeypatch.setattr(backorder_runner, "ntfy_send", lambda headers, body="": None)


def _rows(*domains):
    return [(d, "2026-06-02", "2026-07-03") for d in domains]


def _add_register(status, json_body):
    responses.add(
        responses.POST, "https://api.microware.hu/domains/register",
        json=json_body, status=status,
    )


@responses.activate
def test_run_submits_present_unplaced_domain(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = False
    _add_register(201, {"domain": {"orderid": 77}, "result": {"code": 201, "message": "Created"}})

    run_auto_backorders(cfg, _rows("babakocsi.hu", "other.hu"), tmp_path)

    placed = load_placed(tmp_path / "auto_backorder_state.json")
    assert placed["babakocsi.hu"]["orderid"] == 77
    assert "babakocsi.hu.backorder" in responses.calls[0].request.body
    assert (tmp_path / "backorder.log").exists()


@responses.activate
def test_run_skips_already_placed(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = False
    mark_placed(tmp_path / "auto_backorder_state.json", "babakocsi.hu", 1)
    _add_register(201, {"domain": {"orderid": 77}, "result": {"code": 201}})

    run_auto_backorders(cfg, _rows("babakocsi.hu"), tmp_path)

    assert len(responses.calls) == 0


@responses.activate
def test_run_skips_domain_not_on_list(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = False
    _add_register(201, {"domain": {"orderid": 77}, "result": {"code": 201}})

    run_auto_backorders(cfg, _rows("unrelated.hu"), tmp_path)

    assert len(responses.calls) == 0
    assert load_placed(tmp_path / "auto_backorder_state.json") == {}


def test_run_dry_run_does_not_mark_placed(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = True

    run_auto_backorders(cfg, _rows("babakocsi.hu"), tmp_path)

    assert load_placed(tmp_path / "auto_backorder_state.json") == {}
    assert (tmp_path / "dry_run.log").exists()


@responses.activate
def test_run_failure_does_not_mark_placed(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = False
    _add_register(400, {"result": {"code": 400, "message": "10256: not available"}})

    run_auto_backorders(cfg, _rows("babakocsi.hu"), tmp_path)

    assert load_placed(tmp_path / "auto_backorder_state.json") == {}
    assert (tmp_path / "backorder.log").exists()


@responses.activate
def test_run_bypasses_daily_cap_state(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = False
    _add_register(201, {"domain": {"orderid": 77}, "result": {"code": 201}})

    run_auto_backorders(cfg, _rows("babakocsi.hu"), tmp_path)

    assert not (tmp_path / "daily_count.json").exists()
    assert not (tmp_path / "submitted_today.json").exists()


@responses.activate
def test_run_noop_without_watched_domains(tmp_path, cfg):
    cfg.pop("auto_backorder_domains", None)
    _add_register(201, {"domain": {"orderid": 77}, "result": {"code": 201}})

    run_auto_backorders(cfg, _rows("babakocsi.hu"), tmp_path)

    assert len(responses.calls) == 0


@responses.activate
def test_run_continues_after_one_domain_fails(tmp_path, cfg):
    # First watched domain succeeds, second fails: the loop must attempt both,
    # placing only the successful one (placed is loaded once, not re-read mid-loop).
    cfg["auto_backorder_domains"] = ["a.hu", "b.hu"]
    cfg["backorder"]["dry_run"] = False
    _add_register(201, {"domain": {"orderid": 1}, "result": {"code": 201}})
    _add_register(400, {"result": {"code": 400, "message": "10256: not available"}})

    run_auto_backorders(cfg, _rows("a.hu", "b.hu"), tmp_path)

    placed = load_placed(tmp_path / "auto_backorder_state.json")
    assert placed["a.hu"]["orderid"] == 1
    assert "b.hu" not in placed
    assert len(responses.calls) == 2


def test_main_auto_backorders_and_skips_manual_notify(tmp_path, cfg, monkeypatch):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = False
    cfg["source_url"] = "http://example/parkolas"
    cfg["notify_on"] = {"short": False, "dictionary": False, "compound": False, "keywords": True, "all_numeric": False}
    cfg["keywords"] = ["babakocsi", "kave"]
    cfg["ignore_substrings"] = []
    cfg["wordlist_languages"] = ["en", "hu"]
    cfg["min_word_zipf_frequency"] = 3.0
    cfg["min_word_length"] = 3

    monkeypatch.setattr(check, "load_config", lambda: cfg)
    monkeypatch.setattr(check, "ROOT", tmp_path)
    monkeypatch.setattr(check, "SEEN_PATH", tmp_path / "seen.json")
    (tmp_path / "seen.json").write_text('{"old.hu": "2026-06-01"}', encoding="utf-8")
    monkeypatch.setattr(check, "run_ip_guard", lambda: None)
    monkeypatch.setattr(check, "fetch_domains", lambda url: [
        ("babakocsi.hu", "2026-06-02", "2026-07-03"),
        ("kave.hu", "2026-06-02", "2026-07-03"),
    ])
    monkeypatch.setattr(
        "auto_backorder.register_backorder",
        lambda domain, c, **k: RegisterResult(success=True, mode="live", api_code=201, order_id=99),
    )
    notified = []
    monkeypatch.setattr(check, "ntfy_send", lambda headers, body="": notified.append(headers.get("Title", "")))

    rc = check.main()

    assert rc == 0
    placed = load_placed(tmp_path / "auto_backorder_state.json")
    assert placed["babakocsi.hu"]["orderid"] == 99
    # watched domain suppressed from manual notify...
    assert not any("babakocsi.hu" in title for title in notified)
    # ...but the non-watched match is still notified (proves the loop isn't dead)
    assert any("kave.hu" in title for title in notified)


@responses.activate
def test_attempt_now_skips_already_placed(tmp_path, cfg):
    # The per-minute retry must no-op (no HTTP, no cost) once placed.
    cfg["backorder"]["dry_run"] = False
    mark_placed(tmp_path / "auto_backorder_state.json", "babakocsi.hu", 1)
    _add_register(201, {"domain": {"orderid": 9}, "result": {"code": 201}})

    placed = attempt_watched_now(cfg, tmp_path, "babakocsi.hu")

    assert placed is True
    assert len(responses.calls) == 0


@responses.activate
def test_attempt_now_success_marks_placed_and_pushes(tmp_path, cfg, monkeypatch):
    cfg["backorder"]["dry_run"] = False
    _add_register(201, {"domain": {"orderid": 77}, "result": {"code": 201, "message": "Created"}})
    pushes = []
    monkeypatch.setattr(auto_backorder, "result_push", lambda *a, **k: pushes.append(a))

    placed = attempt_watched_now(cfg, tmp_path, "babakocsi.hu")

    assert placed is True
    assert load_placed(tmp_path / "auto_backorder_state.json")["babakocsi.hu"]["orderid"] == 77
    assert len(pushes) == 1
    assert (tmp_path / "backorder.log").exists()


@responses.activate
def test_attempt_now_failure_logs_but_does_not_push(tmp_path, cfg, monkeypatch):
    # 24/7 retries hit 10258 every minute: log every attempt for audit, but do
    # NOT push — otherwise the phone gets ~1440 rejection notifications a day.
    cfg["backorder"]["dry_run"] = False
    _add_register(400, {"result": {"code": 400, "errorno": 10258, "errormsg": "Domain already exist's"}})
    pushes = []
    monkeypatch.setattr(auto_backorder, "result_push", lambda *a, **k: pushes.append(a))

    placed = attempt_watched_now(cfg, tmp_path, "babakocsi.hu")

    assert placed is False
    assert load_placed(tmp_path / "auto_backorder_state.json") == {}
    assert len(pushes) == 0
    log = (tmp_path / "backorder.log").read_text(encoding="utf-8")
    assert "10258" in log


def test_run_swallows_register_exception_and_continues(tmp_path, cfg, monkeypatch):
    # A network/API error on one domain must not propagate (it would abort the
    # rest of check.py) and must not mark it placed; the next watched domain is
    # still attempted.
    cfg["auto_backorder_domains"] = ["boom.hu", "ok.hu"]
    cfg["backorder"]["dry_run"] = False
    calls = []

    def fake_register(domain, c, **k):
        calls.append(domain)
        if domain == "boom.hu":
            raise ConnectionError("microware unreachable")
        return RegisterResult(success=True, mode="live", api_code=201, order_id=5)

    monkeypatch.setattr("auto_backorder.register_backorder", fake_register)

    run_auto_backorders(cfg, _rows("boom.hu", "ok.hu"), tmp_path)

    placed = load_placed(tmp_path / "auto_backorder_state.json")
    assert "boom.hu" not in placed
    assert placed["ok.hu"]["orderid"] == 5
    assert calls == ["boom.hu", "ok.hu"]
