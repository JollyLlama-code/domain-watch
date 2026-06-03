"""Automatic backorder for user-chosen watched domains.

A watched domain (config `auto_backorder_domains`) is backordered the moment
it appears on the parking list, bypassing the daily cap, retried every run
until a live 201, then recorded in auto_backorder_state.json and never
resubmitted. Real money: charge only on a successful catch at the drop.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from backorder_runner import log_backorder, result_push
from microware_client import register_backorder


def load_placed(state_path) -> dict:
    """Map of domain -> {"orderid": int|None, "ts": iso} for domains already
    successfully backordered. Missing/corrupt file -> {}."""
    state_path = Path(state_path)
    if not state_path.exists():
        return {}
    try:
        return dict(json.loads(state_path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        return {}


def mark_placed(state_path, domain: str, orderid) -> None:
    """Record a successful backorder. Atomic (temp + rename)."""
    state_path = Path(state_path)
    placed = load_placed(state_path)
    placed[domain] = {
        "orderid": orderid,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=state_path.parent, delete=False, suffix=".tmp"
    ) as f:
        json.dump(placed, f, ensure_ascii=False, indent=2, sort_keys=True)
        tmp = f.name
    os.replace(tmp, state_path)


def attempt_watched_now(cfg: dict, state_dir, domain: str) -> bool:
    """Submit one live backorder for `domain` WITHOUT consulting the parking
    list — used by the standalone per-minute retry task (retry_backorder.py),
    which runs 24/7 with no scrape. Honors placed-state and dry_run, bypasses
    the daily cap. Logs every live attempt for audit, but pushes ntfy ONLY on
    success: at one attempt per minute a rejection push (e.g. 10258) would
    flood the phone ~1440x/day. Returns True if the domain is placed (already
    or now). Money-safe: charge only on a real 201 catch.
    """
    state_dir = Path(state_dir)
    state_path = state_dir / "auto_backorder_state.json"
    if domain in load_placed(state_path):
        return True
    try:
        result = register_backorder(
            domain,
            cfg,
            dry_run=cfg["backorder"]["dry_run"],
            log_path=str(state_dir / "dry_run.log"),
        )
    except Exception as exc:  # noqa: BLE001 — network/API error, retried next minute
        print(f"retry-backorder {domain} -> ERROR: {exc}")
        return False
    print(f"retry-backorder {domain} -> {result.mode}, success={result.success}")
    if result.mode != "live":
        return bool(result.success)  # dry_run: synthetic success, no state change
    log_backorder(state_dir, domain, result)
    if result.success:
        result_push(domain, result, prefix="AUTO")
        mark_placed(state_path, domain, result.order_id)
        return True
    return False


def run_auto_backorders(cfg: dict, rows, state_dir) -> None:
    """For each watched domain present on the parking list and not yet
    successfully placed, submit a live backorder (cap bypassed). Records
    success in auto_backorder_state.json; failures are retried next run."""
    watched = cfg.get("auto_backorder_domains", [])
    if not watched:
        return
    state_dir = Path(state_dir)
    state_path = state_dir / "auto_backorder_state.json"
    placed = load_placed(state_path)
    present = {domain for domain, _parked, _release in rows}

    for domain in watched:
        if domain in placed or domain not in present:
            continue
        # Isolate per-domain failures: a microware outage (network error) must
        # not abort the rest of check.py (the normal notify loop + seen.json
        # save). No 201 -> no mark_placed -> retried next run. Money-safe.
        try:
            result = register_backorder(
                domain,
                cfg,
                dry_run=cfg["backorder"]["dry_run"],
                log_path=str(state_dir / "dry_run.log"),
            )
        except Exception as exc:  # noqa: BLE001 — network/API errors retried next run
            print(f"auto-backorder {domain} -> ERROR: {exc}")
            continue
        print(f"auto-backorder {domain} -> {result.mode}, success={result.success}")
        if result.mode == "live":
            log_backorder(state_dir, domain, result)
            result_push(domain, result, prefix="AUTO")
            if result.success:
                mark_placed(state_path, domain, result.order_id)
