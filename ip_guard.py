"""Watch the server's public IP so we know whether it's still whitelisted
at microware.

The microware API only accepts calls from whitelisted IPs, and this server
has a dynamic IP. Each run compares the current public IP against the last
one we recorded; on change the caller (check.py) pushes an ntfy alert so the
user can re-whitelist before a live backorder fails with 10401. This module
stays pure + network only — it sends no notifications itself.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "whitelisted_ip.json"

_IPIFY_URL = "https://api.ipify.org"


def current_public_ip(timeout: int = 5) -> str | None:
    """Return the server's current public IP, or None on any failure."""
    try:
        resp = requests.get(_IPIFY_URL, timeout=timeout)
        resp.raise_for_status()
        ip = resp.text.strip()
        return ip or None
    except Exception:
        return None


def load_known_ip(path: Path = STATE_PATH) -> str | None:
    """Return the last IP we recorded, or None if there is no state yet."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("ip")
    except (json.JSONDecodeError, OSError):
        return None


def save_known_ip(ip: str, noticed_iso: str, path: Path = STATE_PATH) -> None:
    path.write_text(
        json.dumps({"ip": ip, "noticed": noticed_iso}, indent=2) + "\n",
        encoding="utf-8",
    )


def evaluate(
    current_ip: str | None, known_ip: str | None
) -> tuple[str, str, str] | None:
    """Pure decision: should we alert the user?

    Returns (action, title, body) where action is "initial" or "changed",
    or None when there is nothing to do (lookup failed, or IP unchanged).
    """
    if current_ip is None:
        return None
    if known_ip is None:
        return (
            "initial",
            "domain-watch: kiindulo szerver IP",
            f"Kiindulo szerver IP: {current_ip}. Whitelisteld a microware "
            "portalon (admin.microware.hu -> API hozzaferes beallitasa).",
        )
    if current_ip != known_ip:
        return (
            "changed",
            "domain-watch: szerver IP valtozott",
            f"Szerver IP valtozott: {known_ip} -> {current_ip}. Whitelisteld "
            "az uj IP-t a microware portalon, kulonben az eles backorder "
            "10401-gyel elhal.",
        )
    return None
