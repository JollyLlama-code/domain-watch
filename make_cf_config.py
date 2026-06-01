"""Generate the cloudflared config.yml correctly.

Reads the tunnel UUID from the existing credentials JSON in
~/.cloudflared/ (avoids hard-coding it), writes a valid config.yml
that routes backorder.babakocsiszakaruhaz.hu -> localhost:8000.

Run on the server:
    python make_cf_config.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HOSTNAME = "backorder.babakocsiszakaruhaz.hu"
LOCAL_SERVICE = "http://localhost:8000"


def main() -> int:
    cf_dir = Path.home() / ".cloudflared"
    if not cf_dir.exists():
        print(f"ERROR: {cf_dir} does not exist. Run `cloudflared tunnel login` first.")
        return 1

    json_files = sorted(cf_dir.glob("*.json"))
    if not json_files:
        print(f"ERROR: no tunnel credentials json found in {cf_dir}.")
        print("Run `cloudflared tunnel create domain-watch-backorder` first.")
        return 1

    if len(json_files) > 1:
        print(f"WARN: multiple credentials json files found, using the newest: {json_files[-1].name}")
    creds = json_files[-1]
    tunnel_id = creds.stem

    content = (
        f"tunnel: {tunnel_id}\n"
        f"credentials-file: {creds.as_posix()}\n"
        "ingress:\n"
        f"  - hostname: {HOSTNAME}\n"
        f"    service: {LOCAL_SERVICE}\n"
        "  - service: http_status:404\n"
    )

    config_path = cf_dir / "config.yml"
    config_path.write_text(content, encoding="utf-8")

    written = config_path.read_text(encoding="utf-8")
    line_count = len(written.splitlines())
    print(f"Wrote {config_path}")
    print(f"  tunnel ID: {tunnel_id}")
    print(f"  hostname: {HOSTNAME}")
    print(f"  local service: {LOCAL_SERVICE}")
    print(f"  bytes: {len(written.encode('utf-8'))}, lines: {line_count}")
    if line_count != 6:
        print(f"FAIL: expected 6 lines, got {line_count}")
        return 2
    print("OK: config.yml looks valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
