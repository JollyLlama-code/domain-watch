"""Auto-loads secrets.env (key=value, one per line) into os.environ on import.

Place this `import load_secrets  # noqa: F401` at the top of any module
that needs the secrets (backorder_api, check). Existing env vars win,
so tests using monkeypatch are unaffected.
"""
from __future__ import annotations

import os
from pathlib import Path

_path = Path(__file__).resolve().parent / "secrets.env"
if _path.exists():
    for raw in _path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())
