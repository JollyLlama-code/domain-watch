"""Send a single test ntfy push with a Backorder action button.

Verifies the tunnel + uvicorn + FastAPI end-to-end from your phone
without waiting for a real domain match. Dry_run mode is respected
on the receiving side, so no real money moves.

Usage:
    python send_test_push.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import load_secrets  # noqa: F401
import check

ROOT = Path(__file__).resolve().parent


def main() -> int:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))

    domain = "test-backorder-button.hu"
    tunnel_url = cfg.get("backorder", {}).get("tunnel_url", "")
    ttl = cfg.get("backorder", {}).get("action_ttl_hours", 24)

    if not tunnel_url:
        print("ERROR: config.json backorder.tunnel_url is empty.")
        return 1

    action_url = check.build_action_url(
        domain, tunnel_url=tunnel_url, ttl_hours=ttl
    )
    if not action_url:
        print(f"ERROR: action URL came out empty. Check BACKORDER_HMAC_SECRET in secrets.env.")
        return 2

    title = f"{domain} - TEST (tap Backorder)"
    headers = check.build_ntfy_headers(title=title, action_url=action_url)

    print(f"Title:   {title}")
    print(f"Actions: {headers.get('Actions', '(none)')}")
    print(f"Topic:   {check.NTFY_TOPIC}")

    check.ntfy_send(
        headers,
        "Test push from send_test_push.py - tap Backorder to verify the "
        "tunnel + uvicorn deploy. Dry_run mode, no real money.",
    )
    print()
    print("OK: push sent. Check your phone for the notification with the Backorder button.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
