"""Tests for check.decide_matches (LLM-vs-fallback decision)."""
from __future__ import annotations

import check


def _cfg(enabled: bool = True) -> dict:
    # Loads real config.json; fallback tests assume "auto" scores above min_word_zipf_frequency.
    cfg = check.load_config()
    cfg.setdefault("llm", {})
    cfg["llm"]["enabled"] = enabled
    return cfg


def test_uses_llm_verdicts_when_present():
    rows = [("szeletelo.hu", "p", "2026-07-01"), ("xkqztr.hu", "p", "2026-07-02")]

    def fake_classify(names, cfg, client=None):
        return {
            "szeletelo.hu": {"valuable": True, "category": "konyhai eszköz"},
            "xkqztr.hu": {"valuable": False, "category": ""},
        }

    matches, llm_failed = check.decide_matches(rows, _cfg(), classify_fn=fake_classify)
    assert llm_failed is False
    assert matches == [("szeletelo.hu", "2026-07-01", ["AI: konyhai eszköz"])]


def test_missing_verdict_treated_as_not_valuable():
    rows = [("szeletelo.hu", "p", "2026-07-01")]

    def fake_classify(names, cfg, client=None):
        return {}  # domain absent from response

    matches, llm_failed = check.decide_matches(rows, _cfg(), classify_fn=fake_classify)
    assert matches == []
    assert llm_failed is False


def test_fallback_to_rules_on_none_sets_flag():
    rows = [("auto.hu", "p", "2026-07-01")]  # "auto" is a dictionary word

    def fake_classify(names, cfg, client=None):
        return None  # simulate failure

    matches, llm_failed = check.decide_matches(rows, _cfg(), classify_fn=fake_classify)
    assert llm_failed is True
    assert any(d == "auto.hu" for d, _r, _reasons in matches)


def test_disabled_uses_rules_without_calling_llm():
    rows = [("auto.hu", "p", "2026-07-01")]

    def fake_classify(names, cfg, client=None):
        raise AssertionError("classify_fn must not be called when llm disabled")

    matches, llm_failed = check.decide_matches(
        rows, _cfg(enabled=False), classify_fn=fake_classify
    )
    assert llm_failed is False
    assert any(d == "auto.hu" for d, _r, _reasons in matches)


def test_valuable_with_empty_category_uses_fallback_label():
    rows = [("borklub.hu", "p", "2026-07-01")]

    def fake_classify(names, cfg, client=None):
        return {"borklub.hu": {"valuable": True, "category": ""}}

    matches, llm_failed = check.decide_matches(rows, _cfg(), classify_fn=fake_classify)
    assert matches == [("borklub.hu", "2026-07-01", ["AI: értékes"])]
    assert llm_failed is False
