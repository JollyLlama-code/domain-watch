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


class _Block:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _Resp:
    def __init__(self, text: str):
        self.content = [_Block(text)]


class _StubMessages:
    def __init__(self, text: str):
        self._text = text
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _Resp(self._text)


class _StubClient:
    def __init__(self, text: str):
        self.messages = _StubMessages(text)


def test_parses_structured_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    payload = json.dumps(
        {
            "results": [
                {"domain": "szeletelo.hu", "valuable": True, "category": "konyhai eszköz"},
                {"domain": "xkqztr.hu", "valuable": False, "category": ""},
            ]
        }
    )
    client = _StubClient(payload)
    out = llm_score.classify_domains(["szeletelo.hu", "xkqztr.hu"], _cfg(), client=client)
    assert out == {
        "szeletelo.hu": {"valuable": True, "category": "konyhai eszköz"},
        "xkqztr.hu": {"valuable": False, "category": ""},
    }
    assert client.messages.last_kwargs["model"] == "claude-haiku-4-5"


class _RaisingMessages:
    def create(self, **kwargs):
        raise RuntimeError("api down")


class _RaisingClient:
    messages = _RaisingMessages()


def test_api_error_returns_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    out = llm_score.classify_domains(["szeletelo.hu"], _cfg(), client=_RaisingClient())
    assert out is None
