"""Tests for backorder_state."""
from __future__ import annotations

from datetime import date

from backorder_state import BackorderState


def test_first_attempt_is_allowed(state_dir):
    state = BackorderState(state_dir, daily_cap=10, today=date(2026, 5, 18))
    allowed, reason = state.attempt_submit("foo.hu")
    assert allowed is True
    assert reason == "ok"


def test_state_files_created_after_first_attempt(state_dir):
    state = BackorderState(state_dir, daily_cap=10, today=date(2026, 5, 18))
    state.attempt_submit("foo.hu")
    assert (state_dir / "daily_count.json").exists()
    assert (state_dir / "submitted_today.json").exists()


def test_resubmit_same_domain_returns_already_submitted(state_dir):
    state = BackorderState(state_dir, daily_cap=10, today=date(2026, 5, 18))
    state.attempt_submit("foo.hu")
    allowed, reason = state.attempt_submit("foo.hu")
    assert allowed is False
    assert reason == "already_submitted_today"


def test_dedup_does_not_consume_cap(state_dir):
    state = BackorderState(state_dir, daily_cap=2, today=date(2026, 5, 18))
    assert state.attempt_submit("a.hu") == (True, "ok")
    for _ in range(3):
        state.attempt_submit("a.hu")
    assert state.attempt_submit("b.hu") == (True, "ok")
    assert state.attempt_submit("c.hu") == (False, "daily_cap_reached")


def test_cap_reached_returns_blocked(state_dir):
    state = BackorderState(state_dir, daily_cap=2, today=date(2026, 5, 18))
    state.attempt_submit("a.hu")
    state.attempt_submit("b.hu")
    allowed, reason = state.attempt_submit("c.hu")
    assert allowed is False
    assert reason == "daily_cap_reached"


def test_count_resets_on_new_day(state_dir):
    yesterday = BackorderState(state_dir, daily_cap=2, today=date(2026, 5, 17))
    yesterday.attempt_submit("a.hu")
    yesterday.attempt_submit("b.hu")
    assert yesterday.attempt_submit("c.hu") == (False, "daily_cap_reached")

    today = BackorderState(state_dir, daily_cap=2, today=date(2026, 5, 18))
    assert today.attempt_submit("d.hu") == (True, "ok")


def test_dedup_resets_on_new_day(state_dir):
    yesterday = BackorderState(state_dir, daily_cap=10, today=date(2026, 5, 17))
    yesterday.attempt_submit("foo.hu")

    today = BackorderState(state_dir, daily_cap=10, today=date(2026, 5, 18))
    assert today.attempt_submit("foo.hu") == (True, "ok")
