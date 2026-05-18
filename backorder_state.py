"""Persisted daily-cap counter + per-domain dedup for backorder submissions.

Two JSON files live in `root`:
- daily_count.json:    {"date": "2026-05-18", "count": 3}
- submitted_today.json: {"foo.hu": "2026-05-18T15:23:11+00:00", ...}

Both reset whenever today differs from the stored date. Writes are atomic
(temp + rename) so a crashed FastAPI doesn't leave corrupted JSON.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path


class BackorderState:
    def __init__(self, root: Path, daily_cap: int, today: date | None = None) -> None:
        self.root = Path(root)
        self.daily_cap = daily_cap
        self._today = today or date.today()
        self.count_path = self.root / "daily_count.json"
        self.dedup_path = self.root / "submitted_today.json"

    def _load_count(self) -> int:
        if not self.count_path.exists():
            return 0
        try:
            data = json.loads(self.count_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return 0
        if data.get("date") != self._today.isoformat():
            return 0
        return int(data.get("count", 0))

    def _load_dedup(self) -> dict[str, str]:
        if not self.dedup_path.exists():
            return {}
        try:
            data = json.loads(self.dedup_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if data.get("date") != self._today.isoformat():
            return {}
        return dict(data.get("domains", {}))

    def _atomic_write(self, path: Path, payload: dict) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.root, delete=False, suffix=".tmp"
        ) as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            tmp = f.name
        os.replace(tmp, path)

    def attempt_submit(self, domain: str) -> tuple[bool, str]:
        """Returns (allowed, reason). Dedup beats cap so re-taps are free."""
        dedup = self._load_dedup()
        if domain in dedup:
            return (False, "already_submitted_today")

        count = self._load_count()
        if count >= self.daily_cap:
            return (False, "daily_cap_reached")

        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        dedup[domain] = now_iso
        self._atomic_write(
            self.dedup_path,
            {"date": self._today.isoformat(), "domains": dedup},
        )
        self._atomic_write(
            self.count_path, {"date": self._today.isoformat(), "count": count + 1}
        )
        return (True, "ok")
