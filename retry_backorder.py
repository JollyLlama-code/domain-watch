"""Standalone per-minute retry of the watched-domain backorder — no scrape.

Run by the BabakocsiBackorder scheduled task every minute, 24/7. For each
`auto_backorder_domains` entry it submits one microware backorder via
`attempt_watched_now`, stopping for good once a domain is successfully placed
(recorded in auto_backorder_state.json). Deliberately does NOT scrape the
domain.hu parking page — that stays the once-daily DomainWatch job, so the
source isn't hammered 1440x/day and our IP doesn't get rate-limited.

Logs every attempt to backorder.log; pushes ntfy only on a successful catch.
Money-safe: microware charges only on a real 201 at the drop.
"""
from __future__ import annotations

import json
from pathlib import Path

import load_secrets  # noqa: F401 — populates MICROWARE_API_PASSWORD from secrets.env
from auto_backorder import attempt_watched_now

ROOT = Path(__file__).resolve().parent


def load_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))


def main() -> int:
    cfg = load_config()
    for domain in cfg.get("auto_backorder_domains", []):
        attempt_watched_now(cfg, ROOT, domain)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
