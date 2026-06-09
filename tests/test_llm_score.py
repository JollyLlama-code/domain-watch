"""Tests for llm_score.classify_domains."""
from __future__ import annotations

import json

import llm_score


def _cfg(cap: int = 200, enabled: bool = True) -> dict:
    return {"llm": {"enabled": enabled, "model": "claude-haiku-4-5",
                    "max_domains_per_run": cap}}


def test_empty_input_returns_empty_dict(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert llm_score.classify_domains([], _cfg()) == {}


def test_cap_exceeded_returns_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    names = [f"d{i}.hu" for i in range(5)]
    assert llm_score.classify_domains(names, _cfg(cap=3)) is None


def test_missing_api_key_returns_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm_score.classify_domains(["szeletelo.hu"], _cfg()) is None
