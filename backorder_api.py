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
import html
import json
import os
import time
from pathlib import Path

import load_secrets  # noqa: F401 — populates os.environ from secrets.env
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from backorder_runner import log_backorder, result_push
from backorder_state import BackorderState
from microware_client import register_backorder

ROOT = Path(__file__).resolve().parent


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
            log_backorder(state_dir, domain, result)
            result_push(domain, result)
        return {
            "success": result.success,
            "mode": result.mode,
            "domain": domain,
            "order_id": result.order_id,
            "api_code": result.api_code,
            "api_message": result.api_message,
            "error_number": result.error_number,
        }

    @app.get("/confirm", response_class=HTMLResponse)
    def confirm(
        domain: str = Query(..., min_length=4, max_length=63),
        exp: int = Query(...),
        sig: str = Query(..., min_length=32, max_length=32),
    ):
        # Render-only: prefetch-safe. The real booking is the POST below.
        safe_domain = html.escape(domain)
        action = (
            f"/backorder?domain={html.escape(domain, quote=True)}"
            f"&exp={exp}&sig={html.escape(sig, quote=True)}"
        )
        return (
            "<!doctype html><html lang='hu'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta name='robots' content='noindex'>"
            f"<title>Lefoglalas: {safe_domain}</title></head><body>"
            f"<h2>{safe_domain}</h2>"
            "<p>Megerosited a backorder leadasat?</p>"
            f'<form method="post" action="{action}">'
            "<button type='submit'>Megerositem a foglalast</button>"
            "</form></body></html>"
        )

    return app


app = create_app()
