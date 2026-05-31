"""ntfy push helper, shared by check.py and backorder_api.py.

Single responsibility: POST a notification to the project's ntfy topic.
Kept separate so backorder_api can alert without importing the heavy
wordfreq-based check module.
"""
from __future__ import annotations

import sys

import requests

# Shared with the Oracle brute-force launcher — subscribe to this topic in the
# ntfy phone app to receive VM-ready pings, domain matches, and IP alerts.
NTFY_TOPIC = "domwatch-m5dcuxgprlov6zea90i1"


def ntfy_send(headers: dict[str, str], body: str = "") -> None:
    try:
        resp = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"ntfy send FAILED: {e}", file=sys.stderr)
