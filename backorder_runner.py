"""Shared audit-log + ntfy result-push for live backorder submissions.

Used by both the ntfy-tap endpoint (backorder_api) and the automatic
watched-domain path (auto_backorder). Kept separate so a single change to
the audit/notify format covers both.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import ip_guard
from notify import ntfy_send


def log_backorder(state_dir: Path | str, domain: str, result) -> None:
    """Append one JSON line per live submission so every real catch attempt
    and its microware outcome leaves an audit trail (dry-runs go to
    dry_run.log instead)."""
    line = json.dumps(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "domain": domain,
            "mode": result.mode,
            "success": result.success,
            "http_status": result.http_status,
            "api_code": result.api_code,
            "api_message": result.api_message,
            "error_number": result.error_number,
            "order_id": result.order_id,
        },
        ensure_ascii=False,
    )
    with open(Path(state_dir) / "backorder.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def result_push(domain: str, result, prefix: str = "") -> None:
    """Send a follow-up ntfy push with the real outcome, since the ntfy app
    only shows a checkmark (HTTP 200) and never the response body. `prefix`
    tags the source (e.g. "AUTO" for the automatic watched-domain path)."""
    if result.success:
        head, tag, prio = "ELKAPVA", "white_check_mark", "high"
        detail = f"order {result.order_id}" if result.order_id else "sikeres katches"
    elif result.error_number == 10401:
        head, tag, prio = "AUTH HIBA 10401", "key", "urgent"
        known = ip_guard.load_known_ip()
        detail = (
            "Frissitsd a microware whitelistet (admin.microware.hu -> API "
            f"hozzaferes). Utolso ismert IP: {known or 'ismeretlen'}."
        )
    else:
        head, tag, prio = "ELUTASITVA", "x", "default"
        bits = []
        if result.error_number:
            bits.append(f"hiba {result.error_number}")
        if result.api_code is not None:
            bits.append(f"code {result.api_code}")
        if result.api_message:
            bits.append(result.api_message)
        detail = " - ".join(bits) if bits else "nincs reszlet"
    tag_prefix = f"{prefix} " if prefix else ""
    ntfy_send(
        {"Title": f"{tag_prefix}{domain} - {head}", "Tags": tag, "Priority": prio},
        f"{tag_prefix}{domain}: {head}\n{detail}",
    )
