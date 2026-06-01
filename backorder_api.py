"""FastAPI endpoint that handles ntfy `Backorder` taps.

Single endpoint: POST /backorder?domain=X&exp=TS&sig=HEX

Verifies HMAC, enforces cap+dedup, then calls microware_client. Returns
JSON suitable for ntfy + debugging.

Run locally for development:
    uvicorn backorder_api:app --reload --port 8000
In production, runs on the watcher server behind a cloudflared tunnel.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import load_secrets  # noqa: F401 — populates os.environ from secrets.env
from fastapi import FastAPI, HTTPException, Query

import ip_guard
from backorder_state import BackorderState
from microware_client import register_backorder
from notify import ntfy_send

ROOT = Path(__file__).resolve().parent


def _log_backorder(state_dir: Path, domain: str, result) -> None:
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
    with open(state_dir / "backorder.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _result_push(domain: str, result) -> None:
    """Send a follow-up ntfy push with the real outcome, since the ntfy app
    only shows a checkmark (HTTP 200) and never the response body."""
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
    ntfy_send(
        {"Title": f"{domain} - {head}", "Tags": tag, "Priority": prio},
        f"{domain}: {head}\n{detail}",
    )


def _verify_signature(domain: str, exp: int, sig: str, secret: str) -> bool:
    if exp < int(time.time()):
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{domain}|{exp}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    return hmac.compare_digest(expected, sig)


def create_app(cfg_path: Path | None = None, state_dir: Path | None = None) -> FastAPI:
    cfg_path = Path(cfg_path) if cfg_path else ROOT / "config.json"
    state_dir = Path(state_dir) if state_dir else ROOT

    app = FastAPI(title="domain-watch backorder")

    @app.post("/backorder")
    def backorder(
        domain: str = Query(..., min_length=4, max_length=63),
        exp: int = Query(...),
        sig: str = Query(..., min_length=32, max_length=32),
    ):
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        secret = os.environ.get("BACKORDER_HMAC_SECRET", "")
        if not secret:
            raise HTTPException(status_code=500, detail="server misconfigured")

        if not _verify_signature(domain, exp, sig, secret):
            raise HTTPException(status_code=403, detail="bad signature or expired")

        if not cfg["backorder"]["enabled"]:
            raise HTTPException(status_code=503, detail="backorder disabled")

        state = BackorderState(state_dir, daily_cap=cfg["backorder"]["daily_cap"])
        allowed, reason = state.attempt_submit(domain)
        if not allowed:
            if reason == "already_submitted_today":
                return {
                    "success": True,
                    "mode": "duplicate",
                    "domain": domain,
                    "reason": reason,
                }
            raise HTTPException(status_code=429, detail=reason)

        result = register_backorder(
            domain,
            cfg,
            dry_run=cfg["backorder"]["dry_run"],
            log_path=str(state_dir / "dry_run.log"),
        )
        if result.mode == "live":
            _log_backorder(state_dir, domain, result)
            _result_push(domain, result)
        return {
            "success": result.success,
            "mode": result.mode,
            "domain": domain,
            "order_id": result.order_id,
            "api_code": result.api_code,
            "api_message": result.api_message,
            "error_number": result.error_number,
        }

    return app


app = create_app()
