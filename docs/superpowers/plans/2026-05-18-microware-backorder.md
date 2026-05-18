# Microware backorder integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one-tap backorder from ntfy notifications: user taps "Backorder" on a `.hu` domain push → microware `/domains/register` is called → catch attempt placed, with daily cost cap and dry_run safety.

**Architecture:** New `microware_client.py` (HTTP Basic API client), `backorder_state.py` (daily counter + dedup), `backorder_api.py` (FastAPI HMAC-verified endpoint). `check.py` refactored to per-match push with HMAC-signed Action URL. Three layers of safety: `enabled:false` gate, `dry_run:true` default, 10/day cap. See `docs/superpowers/specs/2026-05-18-microware-backorder-design.md`.

**Tech Stack:** Python 3.12, `requests`, `fastapi`, `uvicorn[standard]`, `pydantic`, `pytest`, `httpx` (for FastAPI TestClient).

**Out of scope this session** (deferred until LPNTY access): cloudflared tunnel, Task Scheduler entry for FastAPI, microware portal IP whitelist + API password, production smoke test, `enabled:true` cutover.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `microware_client.py` | NEW | Build registration request body, call microware API with HTTP Basic, map response to `RegisterResult`. `HU_DECLARATION_TEXT` constant. dry_run short-circuit. |
| `backorder_state.py` | NEW | Persisted daily count + per-domain dedup. Atomic writes. Midnight reset by date stamp. |
| `backorder_api.py` | NEW | FastAPI app. Single endpoint `POST /backorder`. HMAC verify, enabled-flag check, state check, call client, return JSON. |
| `tests/conftest.py` | NEW | Shared pytest fixtures: temp config dir, fake state files, fake clock. |
| `tests/test_microware_client.py` | NEW | Unit tests for body construction, dry_run, success/error mapping. Uses `responses` library or `unittest.mock` for the HTTP layer. |
| `tests/test_backorder_state.py` | NEW | Daily cap, dedup, midnight reset, atomic write. |
| `tests/test_backorder_api.py` | NEW | FastAPI TestClient: signed/unsigned requests, expired, cap reached, dry_run. |
| `tests/test_check_notification.py` | NEW | URL signing format, per-match notification body, fallback when tunnel_url empty. |
| `check.py` | MODIFY | Per-match push, build signed URL, optional fallback. |
| `config.json` | MODIFY | Add `microware` and `backorder` sections. |
| `secrets.env.example` | NEW | Template for `MICROWARE_API_PASSWORD` and `BACKORDER_HMAC_SECRET`. Never commit actual `secrets.env`. |
| `requirements.txt` | MODIFY | Add fastapi/uvicorn/pydantic/pytest/httpx/responses. |
| `.gitignore` | MODIFY | Add `secrets.env`, `dry_run.log`, `daily_count.json`, `submitted_today.json`. |
| `pyproject.toml` | NEW | Minimal `[tool.pytest.ini_options]` with `testpaths = ["tests"]`. |

---

## Task 1: Setup — deps, config, gitignore, secrets template

**Files:**
- Modify: `requirements.txt`
- Modify: `config.json`
- Modify: `.gitignore`
- Create: `secrets.env.example`
- Create: `pyproject.toml`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Add dependencies to `requirements.txt`**

Current file ends with `wordfreq`. Append:

```
fastapi==0.115.5
uvicorn[standard]==0.32.1
pydantic==2.10.3
pytest==8.3.4
httpx==0.28.1
responses==0.25.3
```

Versions are pinned for reproducibility; bump together later if needed.

- [ ] **Step 2: Extend `config.json`**

Existing top-level keys stay. Add `microware` and `backorder` sections so the final file is:

```json
{
  "source_url": "https://info.domain.hu/parkolas/hu/ido.html",
  "max_short_length": 4,
  "wordlist_languages": ["en", "hu"],
  "min_word_zipf_frequency": 3.0,
  "min_word_length": 3,
  "compound_min_part_zipf": 4.0,
  "compound_min_part_length": 4,
  "notify_on": {
    "short": true,
    "dictionary": true,
    "compound": false,
    "keywords": true,
    "all_numeric": true
  },
  "keywords": [],
  "ignore_substrings": ["xn--"],
  "microware": {
    "base_url": "https://api.microware.hu",
    "username": "PLACEHOLDER_admin_username",
    "owner_contact_id": "PLACEHOLDER_from_portal",
    "ns1": "ns1.microware.hu",
    "ns2": "ns2.microware.hu",
    "registration_years": 2,
    "domain_type": "1f"
  },
  "backorder": {
    "enabled": false,
    "dry_run": true,
    "daily_cap": 10,
    "tunnel_url": "",
    "action_ttl_hours": 24
  }
}
```

- [ ] **Step 3: Extend `.gitignore`**

Current `.gitignore` already contains `today_matches.txt` and similar. Append:

```
secrets.env
dry_run.log
daily_count.json
submitted_today.json
__pycache__/
.pytest_cache/
```

- [ ] **Step 4: Create `secrets.env.example`**

```
# Copy to secrets.env on LPNTY only. Never commit secrets.env.
# Set MICROWARE_API_PASSWORD on https://admin.microware.hu →
#   Beállítások → API hozzáférés beállítása.
MICROWARE_API_PASSWORD=replace_me

# Generate once with:
#   python -c "import secrets; print(secrets.token_hex(32))"
# Must match in check.py (signer) and backorder_api.py (verifier).
BACKORDER_HMAC_SECRET=replace_me
```

- [ ] **Step 5: Create `pyproject.toml`**

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
addopts = "-q"
```

- [ ] **Step 6: Create `tests/__init__.py` (empty file)**

Empty file, just to mark `tests/` as a package so imports work consistently.

- [ ] **Step 7: Create `tests/conftest.py` with shared fixtures**

```python
"""Shared pytest fixtures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def cfg() -> dict:
    """Baseline config used by most tests."""
    return {
        "microware": {
            "base_url": "https://api.microware.hu",
            "username": "testuser",
            "owner_contact_id": "12345",
            "ns1": "ns1.microware.hu",
            "ns2": "ns2.microware.hu",
            "registration_years": 2,
            "domain_type": "1f",
        },
        "backorder": {
            "enabled": True,
            "dry_run": False,
            "daily_cap": 10,
            "tunnel_url": "https://tunnel.example/backorder",
            "action_ttl_hours": 24,
        },
    }


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path
```

- [ ] **Step 8: Install deps and confirm pytest discovers nothing yet (no tests written)**

Run:
```
python -m pip install -r requirements.txt
python -m pytest
```
Expected: `no tests ran` (exit 5), no import errors.

- [ ] **Step 9: Commit**

```
git add requirements.txt config.json .gitignore secrets.env.example pyproject.toml tests/__init__.py tests/conftest.py
git commit -m "chore: scaffold backorder integration deps and config"
```

---

## Task 2: `microware_client.py` (TDD)

**Files:**
- Create: `microware_client.py`
- Create: `tests/test_microware_client.py`

- [ ] **Step 1: Write failing test for request body construction**

Create `tests/test_microware_client.py`:

```python
"""Tests for microware_client."""
from __future__ import annotations

import json

import pytest
import responses

from microware_client import (
    HU_DECLARATION_TEXT,
    RegisterResult,
    build_register_body,
    register_backorder,
)


def test_build_register_body_includes_required_hu_fields(cfg):
    body = build_register_body("foo.hu", cfg)
    assert body["domain"] == "foo.hu"
    assert body["years"] == 2
    assert body["ns1"] == "ns1.microware.hu"
    assert body["ns2"] == "ns2.microware.hu"
    assert body["owner"] == "12345"
    assert body["type"] == "1f"
    assert body["declarations"] == HU_DECLARATION_TEXT


def test_hu_declaration_text_has_required_phrases():
    # Spot-check the legally-mandated text isn't accidentally truncated.
    assert "Domainregisztrációs Szabályzat" in HU_DECLARATION_TEXT
    assert "Alternatív Vitarendező Fórum" in HU_DECLARATION_TEXT
```

- [ ] **Step 2: Run test, expect ImportError**

```
python -m pytest tests/test_microware_client.py -v
```
Expected: collection error (module not found).

- [ ] **Step 3: Create minimal `microware_client.py`**

```python
"""Microware Domain API client.

Single responsibility: given a config and a domain name, call
/domains/register (which IS the backorder when the target is in
pre-deletion parking). HTTP Basic auth, password from env.

Real money: the production endpoint charges 2604 Ft per successful
catch. Always go through `register_backorder` so the dry_run gate is
respected.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import requests

# Verbatim from microware API docs page 25. Must NOT be reformatted —
# the registry's HU+EN declaration text is matched literally on the
# registrar side. Keep both Hungarian and English halves.
HU_DECLARATION_TEXT = (
    "Nyilatkozom, hogy ezen domain igénylés kapcsán én vagyok az igénylő vagy "
    "jogosult vagyok az igénylő képviseletében eljárni. Szavatolom, hogy domain "
    "igénylésemben az adatokat a valóságnak megfelelően adtam meg, és tudomásul "
    "veszem, hogy amennyiben a megadott adatok nem valósak vagy az adatok "
    "megváltozását nem jelentem be, az a domain név visszavonását eredményezi. "
    "Megértettem, hogy a faktor adatok feletti rendelkezés megőrzésére különös "
    "figyelmet kell fordítanom. "
    "A [Domainregisztrációs Szabályzatot](https://www.domain.hu/domainregisztracios-szabalyzat/) "
    "megismertem, elfogadom és a mindenkor hatályos Domainregisztrációs "
    "Szabályzat előírásait a domain igénylés és fenntartás teljes tartama alatt "
    "betartom, és magamra vagy az általam képviselt domain igénylőre nézve "
    "kötelezőnek ismerem el. "
    "Kijelentem, hogy az igényléssel és a domain fenntartásával alávetem magam "
    "az [Alternatív Vitarendező Fórum](https://www.domain.hu/panaszkezeles/) "
    "döntéseinek. "
    "Megismertem az [Adatvédelmi Tájékoztatóban](https://www.domain.hu/adatkezeles/) "
    "foglaltakat, és személyes adataimnak az abban foglaltak szerinti kezelését "
    "elfogadom. "
    "Az összes adatot ellenőriztem és helyesek az adatok."
)


@dataclass
class RegisterResult:
    success: bool
    mode: str  # "live" | "dry_run"
    http_status: int | None = None
    api_code: int | None = None
    api_message: str = ""
    error_number: int | None = None
    order_id: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    request_body: dict[str, Any] = field(default_factory=dict)


def build_register_body(domain: str, cfg: dict) -> dict[str, Any]:
    """Construct the /domains/register POST body for a .hu backorder."""
    mw = cfg["microware"]
    return {
        "domain": domain,
        "years": mw["registration_years"],
        "ns1": mw["ns1"],
        "ns2": mw["ns2"],
        "owner": mw["owner_contact_id"],
        "type": mw["domain_type"],
        "declarations": HU_DECLARATION_TEXT,
    }


def register_backorder(
    domain: str, cfg: dict, *, dry_run: bool, log_path: str | None = None
) -> RegisterResult:
    """Submit a backorder. When dry_run, returns synthetic success without
    POSTing. Real call uses HTTP Basic with MICROWARE_API_PASSWORD env."""
    body = build_register_body(domain, cfg)
    if dry_run:
        if log_path:
            import json
            from datetime import datetime, timezone

            line = json.dumps(
                {"ts": datetime.now(timezone.utc).isoformat(), "domain": domain, "body": body},
                ensure_ascii=False,
            )
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return RegisterResult(success=True, mode="dry_run", request_body=body)

    password = os.environ.get("MICROWARE_API_PASSWORD")
    if not password:
        return RegisterResult(
            success=False,
            mode="live",
            api_message="MICROWARE_API_PASSWORD env not set",
            request_body=body,
        )

    url = cfg["microware"]["base_url"].rstrip("/") + "/domains/register"
    resp = requests.post(
        url,
        data=body,
        auth=(cfg["microware"]["username"], password),
        timeout=30,
    )
    return _parse_response(resp, body)


def _parse_response(resp: "requests.Response", body: dict[str, Any]) -> RegisterResult:
    try:
        payload = resp.json()
    except ValueError:
        return RegisterResult(
            success=False,
            mode="live",
            http_status=resp.status_code,
            api_message=f"non-JSON response: {resp.text[:200]}",
            request_body=body,
        )

    result = payload.get("result", {})
    api_code = result.get("code")
    api_message = result.get("message", "")
    order_id = payload.get("domain", {}).get("orderid")
    # The API returns 201 for created. Anything else with an error number
    # in the message — extract it for upstream handling (e.g. 10362 lost catch).
    error_number = None
    if api_code != 201:
        for tok in api_message.replace(":", " ").split():
            if tok.isdigit() and len(tok) == 5:
                error_number = int(tok)
                break

    return RegisterResult(
        success=api_code == 201,
        mode="live",
        http_status=resp.status_code,
        api_code=api_code,
        api_message=api_message,
        error_number=error_number,
        order_id=order_id,
        raw=payload,
        request_body=body,
    )
```

- [ ] **Step 4: Run tests, expect PASS**

```
python -m pytest tests/test_microware_client.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Add success-path test for `register_backorder` (mocked HTTP)**

Append to `tests/test_microware_client.py`:

```python
@responses.activate
def test_register_backorder_success(cfg):
    responses.add(
        responses.POST,
        "https://api.microware.hu/domains/register",
        json={
            "domain": {"orderid": 42, "domainids": "99", "invoiceid": 7},
            "result": {"code": 201, "message": "Created"},
        },
        status=201,
    )
    res = register_backorder("foo.hu", cfg, dry_run=False)
    assert res.success is True
    assert res.mode == "live"
    assert res.order_id == 42
    assert res.api_code == 201
```

Test requires `MICROWARE_API_PASSWORD` env. Add at top of file (after imports):

```python
@pytest.fixture(autouse=True)
def _set_api_password(monkeypatch):
    monkeypatch.setenv("MICROWARE_API_PASSWORD", "testpass")
```

- [ ] **Step 6: Run the new test, expect PASS**

```
python -m pytest tests/test_microware_client.py::test_register_backorder_success -v
```
Expected: 1 passed.

- [ ] **Step 7: Add failure-path test (10362 lost catch)**

Append:

```python
@responses.activate
def test_register_backorder_lost_catch_returns_error_number(cfg):
    responses.add(
        responses.POST,
        "https://api.microware.hu/domains/register",
        json={
            "domain": {},
            "result": {"code": 400, "message": "10362: Failed backorder registration"},
        },
        status=400,
    )
    res = register_backorder("foo.hu", cfg, dry_run=False)
    assert res.success is False
    assert res.error_number == 10362
    assert res.api_code == 400
```

- [ ] **Step 8: Run, expect PASS**

```
python -m pytest tests/test_microware_client.py -v
```
Expected: 4 passed.

- [ ] **Step 9: Add dry_run test (writes log line, does not POST)**

Append:

```python
def test_register_backorder_dry_run_writes_log_and_skips_http(cfg, tmp_path):
    log = tmp_path / "dry_run.log"
    res = register_backorder("bar.hu", cfg, dry_run=True, log_path=str(log))
    assert res.success is True
    assert res.mode == "dry_run"
    assert res.request_body["domain"] == "bar.hu"
    contents = log.read_text(encoding="utf-8")
    assert "bar.hu" in contents
    # If this hit HTTP, `responses` would raise (no mock registered)
```

- [ ] **Step 10: Run all microware_client tests, expect PASS**

```
python -m pytest tests/test_microware_client.py -v
```
Expected: 5 passed.

- [ ] **Step 11: Commit**

```
git add microware_client.py tests/test_microware_client.py
git commit -m "feat: add microware /domains/register client with dry_run"
```

---

## Task 3: `backorder_state.py` (TDD)

**Files:**
- Create: `backorder_state.py`
- Create: `tests/test_backorder_state.py`

- [ ] **Step 1: Write failing test for first-attempt success**

Create `tests/test_backorder_state.py`:

```python
"""Tests for backorder_state."""
from __future__ import annotations

from datetime import date

import pytest

from backorder_state import BackorderState


def test_first_attempt_is_allowed(state_dir):
    state = BackorderState(state_dir, daily_cap=10, today=date(2026, 5, 18))
    allowed, reason = state.attempt_submit("foo.hu")
    assert allowed is True
    assert reason == "ok"


def test_state_files_created_after_first_attempt(state_dir):
    state = BackorderState(state_dir, daily_cap=10, today=date(2026, 5, 18))
    state.attempt_submit("foo.hu")
    assert (state_dir / "daily_count.json").exists()
    assert (state_dir / "submitted_today.json").exists()
```

- [ ] **Step 2: Run, expect ImportError**

```
python -m pytest tests/test_backorder_state.py -v
```
Expected: collection error.

- [ ] **Step 3: Implement `backorder_state.py`**

```python
"""Persisted daily-cap counter + per-domain dedup for backorder submissions.

Two JSON files live next to check.py:
- daily_count.json:    {"date": "2026-05-18", "count": 3}
- submitted_today.json: {"foo.hu": "2026-05-18T15:23:11Z", ...}

Both reset whenever today differs from the stored date. Writes are
atomic (temp + rename) so a crashed FastAPI doesn't leave corrupted
JSON for the next request.
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
        # Drop all entries if date rolled. The file is per-day; entries
        # from yesterday don't dedup today.
        today_iso = self._today.isoformat()
        return {d: ts for d, ts in data.items() if ts.startswith(today_iso)}

    def _atomic_write(self, path: Path, payload: dict) -> None:
        # Tempfile in same dir so os.replace is atomic on Windows + POSIX.
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
        self._atomic_write(self.dedup_path, dedup)
        self._atomic_write(
            self.count_path, {"date": self._today.isoformat(), "count": count + 1}
        )
        return (True, "ok")
```

- [ ] **Step 4: Run, expect PASS**

```
python -m pytest tests/test_backorder_state.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Add dedup test**

Append:

```python
def test_resubmit_same_domain_returns_already_submitted(state_dir):
    state = BackorderState(state_dir, daily_cap=10, today=date(2026, 5, 18))
    state.attempt_submit("foo.hu")
    allowed, reason = state.attempt_submit("foo.hu")
    assert allowed is False
    assert reason == "already_submitted_today"


def test_dedup_does_not_consume_cap(state_dir):
    state = BackorderState(state_dir, daily_cap=2, today=date(2026, 5, 18))
    assert state.attempt_submit("a.hu") == (True, "ok")
    # Three re-taps on a.hu should not exhaust the cap of 2
    for _ in range(3):
        state.attempt_submit("a.hu")
    assert state.attempt_submit("b.hu") == (True, "ok")
    assert state.attempt_submit("c.hu") == (False, "daily_cap_reached")
```

- [ ] **Step 6: Run, expect PASS**

```
python -m pytest tests/test_backorder_state.py -v
```
Expected: 4 passed.

- [ ] **Step 7: Add cap test**

Append:

```python
def test_cap_reached_returns_blocked(state_dir):
    state = BackorderState(state_dir, daily_cap=2, today=date(2026, 5, 18))
    state.attempt_submit("a.hu")
    state.attempt_submit("b.hu")
    allowed, reason = state.attempt_submit("c.hu")
    assert allowed is False
    assert reason == "daily_cap_reached"
```

- [ ] **Step 8: Run, expect PASS**

```
python -m pytest tests/test_backorder_state.py -v
```
Expected: 5 passed.

- [ ] **Step 9: Add midnight-reset test**

Append:

```python
def test_count_resets_on_new_day(state_dir):
    # Yesterday: hit cap of 2
    yesterday = BackorderState(state_dir, daily_cap=2, today=date(2026, 5, 17))
    yesterday.attempt_submit("a.hu")
    yesterday.attempt_submit("b.hu")
    assert yesterday.attempt_submit("c.hu") == (False, "daily_cap_reached")

    # Today: should start fresh
    today = BackorderState(state_dir, daily_cap=2, today=date(2026, 5, 18))
    assert today.attempt_submit("d.hu") == (True, "ok")


def test_dedup_resets_on_new_day(state_dir):
    yesterday = BackorderState(state_dir, daily_cap=10, today=date(2026, 5, 17))
    yesterday.attempt_submit("foo.hu")

    today = BackorderState(state_dir, daily_cap=10, today=date(2026, 5, 18))
    # foo.hu was submitted yesterday, today it should be allowed again
    assert today.attempt_submit("foo.hu") == (True, "ok")
```

- [ ] **Step 10: Run all state tests, expect PASS**

```
python -m pytest tests/test_backorder_state.py -v
```
Expected: 7 passed.

- [ ] **Step 11: Commit**

```
git add backorder_state.py tests/test_backorder_state.py
git commit -m "feat: add backorder daily-cap and dedup state"
```

---

## Task 4: `backorder_api.py` (TDD)

**Files:**
- Create: `backorder_api.py`
- Create: `tests/test_backorder_api.py`

- [ ] **Step 1: Write failing test for happy path with valid signature**

Create `tests/test_backorder_api.py`:

```python
"""Tests for the FastAPI backorder endpoint."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backorder_api import create_app


SECRET = "testsecret"


def sign(domain: str, exp: int) -> str:
    msg = f"{domain}|{exp}".encode("utf-8")
    return hmac.new(SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:32]


def write_cfg(tmp_path: Path, cfg: dict) -> Path:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MICROWARE_API_PASSWORD", "x")
    monkeypatch.setenv("BACKORDER_HMAC_SECRET", SECRET)


def test_valid_dry_run_request_returns_200(tmp_path, cfg):
    cfg["backorder"]["dry_run"] = True
    cfg_path = write_cfg(tmp_path, cfg)
    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)

    exp = int(time.time()) + 3600
    r = client.post(
        "/backorder",
        params={"domain": "foo.hu", "exp": exp, "sig": sign("foo.hu", exp)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["mode"] == "dry_run"
```

- [ ] **Step 2: Run, expect ImportError**

```
python -m pytest tests/test_backorder_api.py -v
```
Expected: collection error.

- [ ] **Step 3: Implement `backorder_api.py`**

```python
"""FastAPI endpoint that handles ntfy `Backorder` taps.

Single endpoint: POST /backorder?domain=X&exp=TS&sig=HEX

Verifies HMAC, enforces cap+dedup, then calls microware_client. Returns
JSON suitable for ntfy + debugging.

Run locally for development:
    uvicorn backorder_api:app --reload --port 8000
On LPNTY (production), runs behind a cloudflared tunnel.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

from backorder_state import BackorderState
from microware_client import register_backorder

ROOT = Path(__file__).resolve().parent


def _verify_signature(domain: str, exp: int, sig: str, secret: str) -> bool:
    if exp < int(time.time()):
        return False
    expected = hmac.new(
        secret.encode("utf-8"), f"{domain}|{exp}".encode("utf-8"), hashlib.sha256
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
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
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
                # Idempotent — re-taps are not errors
                return {"success": True, "mode": "duplicate", "domain": domain, "reason": reason}
            raise HTTPException(status_code=429, detail=reason)

        result = register_backorder(
            domain,
            cfg,
            dry_run=cfg["backorder"]["dry_run"],
            log_path=str(state_dir / "dry_run.log"),
        )
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


# Module-level app for `uvicorn backorder_api:app` (production runs this).
app = create_app()
```

- [ ] **Step 4: Run, expect PASS**

```
python -m pytest tests/test_backorder_api.py::test_valid_dry_run_request_returns_200 -v
```
Expected: 1 passed.

- [ ] **Step 5: Add bad-signature test**

Append:

```python
def test_bad_signature_returns_403(tmp_path, cfg):
    cfg_path = write_cfg(tmp_path, cfg)
    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)

    exp = int(time.time()) + 3600
    r = client.post(
        "/backorder",
        params={"domain": "foo.hu", "exp": exp, "sig": "0" * 32},
    )
    assert r.status_code == 403
```

- [ ] **Step 6: Add expired-signature test**

Append:

```python
def test_expired_signature_returns_403(tmp_path, cfg):
    cfg_path = write_cfg(tmp_path, cfg)
    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)

    exp = int(time.time()) - 60  # already expired
    r = client.post(
        "/backorder",
        params={"domain": "foo.hu", "exp": exp, "sig": sign("foo.hu", exp)},
    )
    assert r.status_code == 403
```

- [ ] **Step 7: Add disabled-flag test**

Append:

```python
def test_disabled_flag_returns_503(tmp_path, cfg):
    cfg["backorder"]["enabled"] = False
    cfg_path = write_cfg(tmp_path, cfg)
    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)

    exp = int(time.time()) + 3600
    r = client.post(
        "/backorder",
        params={"domain": "foo.hu", "exp": exp, "sig": sign("foo.hu", exp)},
    )
    assert r.status_code == 503
```

- [ ] **Step 8: Add cap-reached test**

Append:

```python
def test_cap_reached_returns_429(tmp_path, cfg):
    cfg["backorder"]["dry_run"] = True
    cfg["backorder"]["daily_cap"] = 1
    cfg_path = write_cfg(tmp_path, cfg)
    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)

    exp = int(time.time()) + 3600
    r1 = client.post(
        "/backorder",
        params={"domain": "a.hu", "exp": exp, "sig": sign("a.hu", exp)},
    )
    assert r1.status_code == 200
    r2 = client.post(
        "/backorder",
        params={"domain": "b.hu", "exp": exp, "sig": sign("b.hu", exp)},
    )
    assert r2.status_code == 429
```

- [ ] **Step 9: Add idempotent re-tap test**

Append:

```python
def test_resubmit_same_domain_returns_200_duplicate(tmp_path, cfg):
    cfg["backorder"]["dry_run"] = True
    cfg_path = write_cfg(tmp_path, cfg)
    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)

    exp = int(time.time()) + 3600
    p = {"domain": "foo.hu", "exp": exp, "sig": sign("foo.hu", exp)}
    r1 = client.post("/backorder", params=p)
    r2 = client.post("/backorder", params=p)
    assert r1.status_code == 200 and r1.json()["mode"] == "dry_run"
    assert r2.status_code == 200 and r2.json()["mode"] == "duplicate"
```

- [ ] **Step 10: Run all api tests, expect PASS**

```
python -m pytest tests/test_backorder_api.py -v
```
Expected: 5 passed.

- [ ] **Step 11: Commit**

```
git add backorder_api.py tests/test_backorder_api.py
git commit -m "feat: add FastAPI backorder endpoint with HMAC verify"
```

---

## Task 5: `check.py` per-match refactor (TDD)

**Files:**
- Modify: `check.py`
- Create: `tests/test_check_notification.py`

The current `check.py` builds one batched ntfy push. Refactor so each new match is its own push with an `Actions` header pointing at the FastAPI endpoint, HMAC-signed. When `tunnel_url` is empty, fall back to plain title-only push.

- [ ] **Step 1: Write failing test for URL signing helper**

Create `tests/test_check_notification.py`:

```python
"""Tests for check.py URL signing and per-match notification format."""
from __future__ import annotations

import hashlib
import hmac

import pytest

from check import build_action_url, build_ntfy_headers


SECRET = "topsecret"


def _expected_sig(domain: str, exp: int) -> str:
    msg = f"{domain}|{exp}".encode("utf-8")
    return hmac.new(SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:32]


def test_build_action_url_has_signed_query(monkeypatch):
    monkeypatch.setenv("BACKORDER_HMAC_SECRET", SECRET)
    url = build_action_url(
        "foo.hu", tunnel_url="https://tun.example/backorder", ttl_hours=24, now=1_700_000_000
    )
    expected_exp = 1_700_000_000 + 24 * 3600
    expected_sig = _expected_sig("foo.hu", expected_exp)
    assert f"domain=foo.hu" in url
    assert f"exp={expected_exp}" in url
    assert f"sig={expected_sig}" in url
    assert url.startswith("https://tun.example/backorder?")
```

- [ ] **Step 2: Run, expect ImportError (functions don't exist yet)**

```
python -m pytest tests/test_check_notification.py -v
```
Expected: collection error.

- [ ] **Step 3: Add `build_action_url` to `check.py`**

Add these imports at the top of `check.py` (after the existing imports):

```python
import hashlib
import hmac
import os
import time
```

Add this function before `ntfy_send`:

```python
def build_action_url(
    domain: str, *, tunnel_url: str, ttl_hours: int, now: int | None = None
) -> str:
    """Construct the HMAC-signed Backorder action URL.

    Empty `tunnel_url` returns "" — caller falls back to a plain push.
    """
    if not tunnel_url:
        return ""
    secret = os.environ.get("BACKORDER_HMAC_SECRET", "")
    if not secret:
        return ""
    exp = (now if now is not None else int(time.time())) + ttl_hours * 3600
    sig = hmac.new(
        secret.encode("utf-8"), f"{domain}|{exp}".encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]
    return f"{tunnel_url}?domain={domain}&exp={exp}&sig={sig}"
```

- [ ] **Step 4: Run, expect PASS**

```
python -m pytest tests/test_check_notification.py::test_build_action_url_has_signed_query -v
```
Expected: 1 passed.

- [ ] **Step 5: Write failing test for ntfy headers (with and without action)**

Append:

```python
def test_build_ntfy_headers_with_action(monkeypatch):
    monkeypatch.setenv("BACKORDER_HMAC_SECRET", SECRET)
    headers = build_ntfy_headers(
        title="foo.hu — en word",
        action_url="https://tun.example/backorder?domain=foo.hu&exp=1&sig=ab",
    )
    assert headers["Title"] == "foo.hu — en word"
    assert "Actions" in headers
    assert "Backorder" in headers["Actions"]
    assert "https://tun.example/backorder" in headers["Actions"]
    assert "clear=true" in headers["Actions"]


def test_build_ntfy_headers_without_action():
    headers = build_ntfy_headers(title="foo.hu — en word", action_url="")
    assert headers["Title"] == "foo.hu — en word"
    assert "Actions" not in headers
```

- [ ] **Step 6: Run, expect ImportError on `build_ntfy_headers`**

```
python -m pytest tests/test_check_notification.py -v
```
Expected: 1 passed (URL test), 2 errors (missing function).

- [ ] **Step 7: Add `build_ntfy_headers` to `check.py`**

Add before `ntfy_send`:

```python
def build_ntfy_headers(*, title: str, action_url: str) -> dict[str, str]:
    """Headers for a single per-match ntfy push. Adds Backorder action when
    action_url is non-empty; otherwise sends a plain push."""
    headers = {"Title": title}
    if action_url:
        # ntfy Actions format: action_type, label, url, [key=value, ...]
        headers["Actions"] = f"http, Backorder, {action_url}, method=POST, clear=true"
    return headers
```

- [ ] **Step 8: Run, expect PASS**

```
python -m pytest tests/test_check_notification.py -v
```
Expected: 3 passed.

- [ ] **Step 9: Refactor `ntfy_send` to accept headers dict and refactor `main()` to per-match**

Replace the existing `ntfy_send` body. Old:

```python
def ntfy_send(title: str, body: str) -> None:
    try:
        resp = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers={"Title": title},
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"ntfy send FAILED: {e}", file=sys.stderr)
```

New:

```python
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
```

Replace the `if matches:` block in `main()`. Old:

```python
    if matches:
        title, body = format_matches(matches)
        print(title + "\n" + body)
        ntfy_send(title, body)
```

New:

```python
    if matches:
        tunnel_url = cfg.get("backorder", {}).get("tunnel_url", "")
        ttl_hours = cfg.get("backorder", {}).get("action_ttl_hours", 24)
        for domain, _release, reasons in matches:
            reason_summary = ", ".join(reasons[:2])  # top 2 reasons fit in title
            title = f"{domain} — {reason_summary}"
            print(title)
            action_url = build_action_url(
                domain, tunnel_url=tunnel_url, ttl_hours=ttl_hours
            )
            headers = build_ntfy_headers(title=title, action_url=action_url)
            ntfy_send(headers, "")
```

Also update `run_test_notification` to use the new shape. Old:

```python
    title, body = format_matches(sample)
    title = "[TEST] " + title
    print(title + "\n" + body)
    ntfy_send(title, body)
    print("Test notification sent.")
    return 0
```

New:

```python
    # Test mode: just send a single batched preview as before (no action button)
    title = f"[TEST] Domain watch - {len(sample)} match"
    body = "\n".join(d for d, _, _ in sample)
    print(title + "\n" + body)
    ntfy_send({"Title": title}, body)
    print("Test notification sent.")
    return 0
```

The `format_matches` function becomes unused — delete it.

- [ ] **Step 10: Run all tests to confirm no regression**

```
python -m pytest -v
```
Expected: all previously-passing tests still pass; the 3 notification tests pass.

- [ ] **Step 11: Manual smoke — dry import check**

```
python -c "import check; print(check.build_action_url('a.hu', tunnel_url='', ttl_hours=24))"
```
Expected: empty string `""` printed (no secret, no tunnel → empty URL → fallback to plain push).

- [ ] **Step 12: Commit**

```
git add check.py tests/test_check_notification.py
git commit -m "feat: per-match ntfy push with HMAC-signed Backorder action"
```

---

## Task 6: Local smoke test

This is **manual** verification that the pieces wire together end-to-end on the user's local machine. No LPNTY required.

**Files:**
- Read-only: `microware_client.py`, `backorder_api.py`, `backorder_state.py`

- [ ] **Step 1: Generate a temporary HMAC secret and export it**

Run in PowerShell:
```powershell
$env:BACKORDER_HMAC_SECRET = (python -c "import secrets; print(secrets.token_hex(32))")
$env:MICROWARE_API_PASSWORD = "dummy_not_used_in_dry_run"
Write-Output "secret length: $($env:BACKORDER_HMAC_SECRET.Length)"
```
Expected: `secret length: 64`.

- [ ] **Step 2: Confirm config.json has dry_run=true and enabled=true for the smoke**

Temporarily edit `config.json` so `backorder.enabled = true` and `backorder.dry_run = true`. **Do NOT commit this change** — revert before commit.

- [ ] **Step 3: Start uvicorn locally**

```powershell
python -m uvicorn backorder_api:app --port 8000
```
Leave running. Expected: `Uvicorn running on http://127.0.0.1:8000`.

- [ ] **Step 4: In a second terminal, build a signed request and POST it**

```powershell
$domain = "smoke-test.hu"
$exp = [int][double]::Parse((Get-Date -UFormat %s)) + 3600
$msg = "$domain|$exp"
$secret = $env:BACKORDER_HMAC_SECRET
$sig = python -c "import hmac,hashlib,os,sys; print(hmac.new(os.environ['BACKORDER_HMAC_SECRET'].encode(),sys.argv[1].encode(),hashlib.sha256).hexdigest()[:32])" $msg
curl.exe -X POST "http://127.0.0.1:8000/backorder?domain=$domain&exp=$exp&sig=$sig"
```
Expected JSON response:
```json
{"success":true,"mode":"dry_run","domain":"smoke-test.hu","order_id":null,"api_code":null,"api_message":"","error_number":null}
```

- [ ] **Step 5: Confirm dry_run.log was written**

```powershell
Get-Content dry_run.log
```
Expected: at least one JSON line containing `"domain": "smoke-test.hu"` and the full request body that *would* have been POSTed to microware.

- [ ] **Step 6: Re-run the same curl — verify idempotent duplicate**

Run the same curl as Step 4. Expected response:
```json
{"success":true,"mode":"duplicate","domain":"smoke-test.hu","reason":"already_submitted_today"}
```

- [ ] **Step 7: POST with a bad signature — expect 403**

```powershell
curl.exe -i -X POST "http://127.0.0.1:8000/backorder?domain=foo.hu&exp=$exp&sig=00000000000000000000000000000000"
```
Expected: `HTTP/1.1 403 Forbidden`.

- [ ] **Step 8: Stop uvicorn, revert config**

`Ctrl+C` in the uvicorn terminal. Revert `config.json` so `enabled: false` and `dry_run: true` are back to safe defaults.

- [ ] **Step 9: Clean up smoke artifacts**

```powershell
Remove-Item dry_run.log, daily_count.json, submitted_today.json -ErrorAction SilentlyContinue
```

- [ ] **Step 10: Final commit (config revert + cleanup confirmation)**

```
git status   # should show ONLY config.json reverted (if you accidentally left flag changes), or no changes
git diff config.json
# If config.json reverted cleanly, no commit needed. If anything strayed, fix and commit.
```

---

## Deferred work (needs friend / LPNTY) — DO NOT do in this session

Recorded here for the next session that has LPNTY access:

1. **Microware portal prep** (user can do solo, no friend needed):
   - Log in to `admin.microware.hu`
   - Beállítások → API hozzáférés beállítása → set API password, whitelist `178.48.104.118`
   - Verify owner contact ID, paste into `config.json` `microware.owner_contact_id`
   - Set `microware.username` to the admin username

2. **LPNTY deployment** (needs friend's access):
   - `git pull` on `C:\domain-watch\`
   - `python -m pip install -r requirements.txt`
   - Create `C:\domain-watch\secrets.env` with both real values; load via env at service start
   - Set up cloudflared tunnel: `cloudflared tunnel create domain-watch-backorder`, point at `http://localhost:8000`
   - Paste tunnel URL into `config.json` `backorder.tunnel_url`
   - Register Task Scheduler entry `BackorderAPI` running uvicorn at boot, S4U as built-in admin
   - Confirm `DomainWatch` task still runs check.py

3. **Production smoke** (in dry_run mode, still enabled:true):
   - Wait for a real match, tap Backorder in ntfy
   - Verify `dry_run.log` on LPNTY records the request body
   - Verify no charge appears in microware billing

4. **Cutover** (real money starts flowing):
   - Pick a deliberately low-value first target domain
   - Edit `config.json`: `backorder.dry_run = false`, `backorder.enabled = true`
   - Restart uvicorn
   - Wait for the chosen match, tap Backorder
   - Confirm: invoice appears in microware, domain lands in user's portfolio if caught
   - If catch fails (error 10362), confirm no charge

---

## Self-review checklist

**Spec coverage** — every section in the spec maps to at least one task:
- Architecture diagram → Tasks 2/3/4 build the three boxes
- HMAC-signed action URL → Task 4 verify, Task 5 sign
- State files atomic write → Task 3 implementation
- dry_run + enabled gates → Task 4 endpoint, default config in Task 1
- Per-match notification + fallback → Task 5
- Config additions → Task 1

**Placeholder scan:** no "TBD", "add error handling", "similar to Task N", or implied code. Every step contains the actual content to write.

**Type consistency:** `RegisterResult` fields (success, mode, order_id, api_code, api_message, error_number, raw, request_body) used identically in Task 2 and Task 4. `BackorderState.attempt_submit` returns `(bool, str)` consistently. `build_action_url` and `build_ntfy_headers` signatures match between Task 5 implementation and test.
