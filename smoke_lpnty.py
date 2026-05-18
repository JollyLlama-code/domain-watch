"""One-shot smoke test for the LPNTY backorder API deployment.

Usage:
    set BACKORDER_HMAC_SECRET=<the secret from secrets.env>
    python smoke_lpnty.py

Posts a signed /backorder request to localhost:8000 in dry_run mode and
prints the response + the most recent dry_run.log entry.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import load_secrets  # noqa: F401 — populates os.environ from secrets.env

ROOT = Path(__file__).resolve().parent


def main() -> int:
    secret = os.environ.get("BACKORDER_HMAC_SECRET", "")
    if len(secret) != 64:
        print(f"FAIL: BACKORDER_HMAC_SECRET env var has length {len(secret)}, expected 64.")
        print("Set it from secrets.env first.")
        return 1

    domain = "smoke-test-lpnty.hu"
    exp = int(time.time()) + 3600
    msg = f"{domain}|{exp}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:32]

    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    url = f"{base}/backorder?domain={domain}&exp={exp}&sig={sig}"
    print(f"POST {url}")

    req = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            body = resp.read().decode()
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read().decode()
    except Exception as e:
        print(f"ERROR: {e}")
        return 2

    print(f"status: {status}")
    print(f"body: {body}")

    log = ROOT / "dry_run.log"
    if log.exists():
        print("--- dry_run.log ---")
        # Print the last 1 line (the request we just made)
        lines = log.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            print(lines[-1])
        else:
            print("(log file empty)")
    else:
        print("(dry_run.log not created)")

    try:
        parsed = json.loads(body)
        if status == 200 and parsed.get("success") and parsed.get("mode") in ("dry_run", "duplicate"):
            print("\nOK: smoke test passed.")
            return 0
    except json.JSONDecodeError:
        pass
    print("\nFAIL: response did not match expected dry_run success.")
    return 3


if __name__ == "__main__":
    sys.exit(main())
