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
