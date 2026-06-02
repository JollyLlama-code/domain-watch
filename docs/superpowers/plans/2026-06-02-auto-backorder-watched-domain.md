# Auto-backorder for a watched domain — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically place a microware backorder for any domain listed in a `auto_backorder_domains` config array the moment it appears on the parking list, bypassing the daily cap, retrying until a live `201`, then never resubmitting.

**Architecture:** `check.py` (already scrapes the list on the 00:00–00:15 schedule) gains a post-fetch step that submits backorders directly via `microware_client`, recording successes in a persistent `auto_backorder_state.json`. The audit-log and result-push helpers move from `backorder_api.py` into a shared `backorder_runner.py` used by both the ntfy-tap path and the new auto path.

**Tech Stack:** Python 3.12, `requests`, `responses` (test mocking), `pytest`, FastAPI (existing endpoint, unchanged behaviour).

---

## File Structure

- **Create `backorder_runner.py`** — shared `log_backorder(state_dir, domain, result)` + `result_push(domain, result, prefix="")`. One responsibility: turn a `RegisterResult` into an audit-log line and an ntfy push.
- **Create `auto_backorder.py`** — persistent placed-state (`load_placed`, `mark_placed`) + `run_auto_backorders(cfg, rows, state_dir)` orchestration. One responsibility: decide which watched domains to submit and drive the submission.
- **Modify `backorder_api.py`** — import the two helpers from `backorder_runner` instead of defining them locally. No behaviour change.
- **Modify `check.py`** — call `run_auto_backorders` after fetching rows; skip watched domains in the manual per-match ntfy loop.
- **Modify `config.json`** — add `auto_backorder_domains: ["babakocsi.hu"]`.
- **Modify `.gitignore`** — add `auto_backorder_state.json`.
- **Create `tests/test_auto_backorder.py`** — unit tests for state + orchestration.
- **Modify `tests/test_backorder_api.py`** — repoint monkeypatch targets (`ntfy_send`, `ip_guard`) to `backorder_runner` after the extraction.

---

## Task 1: Extract `backorder_runner.py` (shared log + push)

**Files:**
- Create: `backorder_runner.py`
- Modify: `backorder_api.py` (remove local `_log_backorder`/`_result_push`, import from runner, update call sites)
- Modify: `tests/test_backorder_api.py` (repoint monkeypatch targets)
- Test: `tests/test_backorder_runner.py` (new — covers the `prefix` arg)

- [ ] **Step 1: Create `backorder_runner.py` with the extracted helpers plus a `prefix` arg**

```python
"""Shared audit-log + ntfy result-push for live backorder submissions.

Used by both the ntfy-tap endpoint (backorder_api) and the automatic
watched-domain path (auto_backorder). Kept separate so a single change to
the audit/notify format covers both.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import ip_guard
from notify import ntfy_send


def log_backorder(state_dir, domain: str, result) -> None:
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
    with open(Path(state_dir) / "backorder.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def result_push(domain: str, result, prefix: str = "") -> None:
    """Send a follow-up ntfy push with the real outcome, since the ntfy app
    only shows a checkmark (HTTP 200) and never the response body. `prefix`
    tags the source (e.g. "AUTO" for the automatic watched-domain path)."""
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
    tag_prefix = f"{prefix} " if prefix else ""
    ntfy_send(
        {"Title": f"{tag_prefix}{domain} - {head}", "Tags": tag, "Priority": prio},
        f"{tag_prefix}{domain}: {head}\n{detail}",
    )
```

- [ ] **Step 2: Write a failing test for the `prefix` arg**

Create `tests/test_backorder_runner.py`:

```python
"""Tests for the shared backorder log + push helpers."""
from __future__ import annotations

import json

import backorder_runner
from backorder_runner import log_backorder, result_push
from microware_client import RegisterResult


def test_result_push_prefix_tags_title_and_body(monkeypatch):
    sent = []
    monkeypatch.setattr(backorder_runner, "ntfy_send",
                        lambda headers, body="": sent.append((headers, body)))
    result = RegisterResult(success=True, mode="live", api_code=201, order_id=7)
    result_push("foo.hu", result, prefix="AUTO")
    headers, body = sent[0]
    assert headers["Title"] == "AUTO foo.hu - ELKAPVA"
    assert body.startswith("AUTO foo.hu: ELKAPVA")
    assert "order 7" in body


def test_result_push_no_prefix_unchanged(monkeypatch):
    sent = []
    monkeypatch.setattr(backorder_runner, "ntfy_send",
                        lambda headers, body="": sent.append((headers, body)))
    result = RegisterResult(success=True, mode="live", api_code=201, order_id=7)
    result_push("foo.hu", result)
    headers, _ = sent[0]
    assert headers["Title"] == "foo.hu - ELKAPVA"


def test_log_backorder_writes_one_json_line(tmp_path):
    result = RegisterResult(success=True, mode="live", api_code=201, order_id=7)
    log_backorder(tmp_path, "foo.hu", result)
    lines = (tmp_path / "backorder.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["domain"] == "foo.hu"
    assert entry["order_id"] == 7
```

- [ ] **Step 3: Run the new test to verify it passes (module already created in Step 1)**

Run: `python -m pytest tests/test_backorder_runner.py -v`
Expected: 3 passed.

- [ ] **Step 4: Update `backorder_api.py` to import from the runner and delete the local copies**

Remove the local `_log_backorder` and `_result_push` function definitions (currently at `backorder_api.py:33-81`). Replace the import block near the top so it reads:

```python
import load_secrets  # noqa: F401 — populates os.environ from secrets.env
from fastapi import FastAPI, HTTPException, Query

import ip_guard
from backorder_runner import log_backorder, result_push
from backorder_state import BackorderState
from microware_client import register_backorder
```

(`ip_guard` and `ntfy_send` are no longer used directly by `backorder_api`; remove the `from notify import ntfy_send` line. Keep `import ip_guard` only if still referenced — after this change it is NOT, so remove `import ip_guard` too.)

In the endpoint body, update the two call sites:

```python
        if result.mode == "live":
            log_backorder(state_dir, domain, result)
            result_push(domain, result)
```

- [ ] **Step 5: Repoint monkeypatch targets in `tests/test_backorder_api.py`**

The two helpers now live in `backorder_runner`, so `result_push` resolves `ntfy_send` and `ip_guard` from that module. Add `import backorder_runner` at the top, and change the three monkeypatch lines:

```python
# in test_10401_triggers_ntfy_alert and test_successful_live_result_pushes_and_logs
# and test_rejected_live_result_pushes_and_logs:
monkeypatch.setattr(backorder_runner, "ntfy_send",
                    lambda headers, body="": alerts.append((headers, body)))

# in test_10401_triggers_ntfy_alert only:
monkeypatch.setattr(backorder_runner.ip_guard, "load_known_ip", lambda: "1.2.3.4")
```

Leave `monkeypatch.setattr(backorder_api, "register_backorder", ...)` unchanged — `register_backorder` is still imported and called inside `backorder_api`.

- [ ] **Step 6: Run the full suite to confirm the refactor is behaviour-preserving**

Run: `python -m pytest tests/test_backorder_api.py tests/test_backorder_runner.py -v`
Expected: all pass (the 10 backorder_api tests + 3 runner tests).

- [ ] **Step 7: Commit**

```bash
git add backorder_runner.py backorder_api.py tests/test_backorder_api.py tests/test_backorder_runner.py
git commit -m "refactor: extract shared backorder log+push into backorder_runner"
```

---

## Task 2: Persistent placed-state in `auto_backorder.py`

**Files:**
- Create: `auto_backorder.py` (state functions only in this task)
- Test: `tests/test_auto_backorder.py`

- [ ] **Step 1: Write failing tests for `load_placed` / `mark_placed`**

Create `tests/test_auto_backorder.py`:

```python
"""Tests for the automatic watched-domain backorder path."""
from __future__ import annotations

import json

import pytest

from auto_backorder import load_placed, mark_placed


def test_load_placed_missing_file_returns_empty(tmp_path):
    assert load_placed(tmp_path / "auto_backorder_state.json") == {}


def test_load_placed_corrupt_file_returns_empty(tmp_path):
    p = tmp_path / "auto_backorder_state.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_placed(p) == {}


def test_mark_placed_then_load_roundtrip(tmp_path):
    p = tmp_path / "auto_backorder_state.json"
    mark_placed(p, "babakocsi.hu", 184517)
    placed = load_placed(p)
    assert "babakocsi.hu" in placed
    assert placed["babakocsi.hu"]["orderid"] == 184517
    assert "ts" in placed["babakocsi.hu"]


def test_mark_placed_preserves_existing_entries(tmp_path):
    p = tmp_path / "auto_backorder_state.json"
    mark_placed(p, "a.hu", 1)
    mark_placed(p, "b.hu", 2)
    placed = load_placed(p)
    assert placed["a.hu"]["orderid"] == 1
    assert placed["b.hu"]["orderid"] == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_auto_backorder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'auto_backorder'`.

- [ ] **Step 3: Create `auto_backorder.py` with the state functions**

```python
"""Automatic backorder for user-chosen watched domains.

A watched domain (config `auto_backorder_domains`) is backordered the moment
it appears on the parking list, bypassing the daily cap, retried every run
until a live 201, then recorded in auto_backorder_state.json and never
resubmitted. Real money: charge only on a successful catch at the drop.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from backorder_runner import log_backorder, result_push
from microware_client import register_backorder


def load_placed(state_path) -> dict:
    """Map of domain -> {"orderid": int|None, "ts": iso} for domains already
    successfully backordered. Missing/corrupt file -> {}."""
    state_path = Path(state_path)
    if not state_path.exists():
        return {}
    try:
        return dict(json.loads(state_path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        return {}


def mark_placed(state_path, domain: str, orderid) -> None:
    """Record a successful backorder. Atomic (temp + rename)."""
    state_path = Path(state_path)
    placed = load_placed(state_path)
    placed[domain] = {
        "orderid": orderid,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=state_path.parent, delete=False, suffix=".tmp"
    ) as f:
        json.dump(placed, f, ensure_ascii=False, indent=2, sort_keys=True)
        tmp = f.name
    os.replace(tmp, state_path)
```

- [ ] **Step 4: Run to verify the four tests pass**

Run: `python -m pytest tests/test_auto_backorder.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add auto_backorder.py tests/test_auto_backorder.py
git commit -m "feat: persistent placed-state for auto-backorder watched domains"
```

---

## Task 3: `run_auto_backorders` orchestration

**Files:**
- Modify: `auto_backorder.py` (add `run_auto_backorders`)
- Test: `tests/test_auto_backorder.py` (add orchestration tests)

- [ ] **Step 1: Add failing orchestration tests**

Append to `tests/test_auto_backorder.py` (add imports `import responses`, `import backorder_runner`, `from auto_backorder import run_auto_backorders` at the top):

```python
@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MICROWARE_API_PASSWORD", "x")


@pytest.fixture(autouse=True)
def _silence_ntfy(monkeypatch):
    monkeypatch.setattr(backorder_runner, "ntfy_send", lambda headers, body="": None)


def _rows(*domains):
    return [(d, "2026-06-02", "2026-07-03") for d in domains]


def _add_register(status, json_body):
    responses.add(
        responses.POST, "https://api.microware.hu/domains/register",
        json=json_body, status=status,
    )


@responses.activate
def test_run_submits_present_unplaced_domain(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = False
    _add_register(201, {"domain": {"orderid": 77}, "result": {"code": 201, "message": "Created"}})

    run_auto_backorders(cfg, _rows("babakocsi.hu", "other.hu"), tmp_path)

    placed = load_placed(tmp_path / "auto_backorder_state.json")
    assert placed["babakocsi.hu"]["orderid"] == 77
    # body carried the .backorder suffix
    assert "curry" not in responses.calls[0].request.body  # sanity
    assert "babakocsi.hu.backorder" in responses.calls[0].request.body
    assert (tmp_path / "backorder.log").exists()


@responses.activate
def test_run_skips_already_placed(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = False
    mark_placed(tmp_path / "auto_backorder_state.json", "babakocsi.hu", 1)
    _add_register(201, {"domain": {"orderid": 77}, "result": {"code": 201}})

    run_auto_backorders(cfg, _rows("babakocsi.hu"), tmp_path)

    assert len(responses.calls) == 0  # no resubmit


@responses.activate
def test_run_skips_domain_not_on_list(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = False
    _add_register(201, {"domain": {"orderid": 77}, "result": {"code": 201}})

    run_auto_backorders(cfg, _rows("unrelated.hu"), tmp_path)

    assert len(responses.calls) == 0
    assert load_placed(tmp_path / "auto_backorder_state.json") == {}


def test_run_dry_run_does_not_mark_placed(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = True

    run_auto_backorders(cfg, _rows("babakocsi.hu"), tmp_path)

    assert load_placed(tmp_path / "auto_backorder_state.json") == {}
    assert (tmp_path / "dry_run.log").exists()


@responses.activate
def test_run_failure_does_not_mark_placed(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = False
    _add_register(400, {"result": {"code": 400, "message": "10256: not available"}})

    run_auto_backorders(cfg, _rows("babakocsi.hu"), tmp_path)

    assert load_placed(tmp_path / "auto_backorder_state.json") == {}
    # the failed attempt is still logged for audit
    assert (tmp_path / "backorder.log").exists()


@responses.activate
def test_run_bypasses_daily_cap_state(tmp_path, cfg):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = False
    _add_register(201, {"domain": {"orderid": 77}, "result": {"code": 201}})

    run_auto_backorders(cfg, _rows("babakocsi.hu"), tmp_path)

    # the cap path (BackorderState) is never touched
    assert not (tmp_path / "daily_count.json").exists()
    assert not (tmp_path / "submitted_today.json").exists()


@responses.activate
def test_run_noop_without_watched_domains(tmp_path, cfg):
    cfg.pop("auto_backorder_domains", None)
    _add_register(201, {"domain": {"orderid": 77}, "result": {"code": 201}})

    run_auto_backorders(cfg, _rows("babakocsi.hu"), tmp_path)

    assert len(responses.calls) == 0
```

- [ ] **Step 2: Run to verify the orchestration tests fail**

Run: `python -m pytest tests/test_auto_backorder.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_auto_backorders'`.

- [ ] **Step 3: Implement `run_auto_backorders` in `auto_backorder.py`**

Append to `auto_backorder.py`:

```python
def run_auto_backorders(cfg: dict, rows, state_dir) -> None:
    """For each watched domain present on the parking list and not yet
    successfully placed, submit a live backorder (cap bypassed). Records
    success in auto_backorder_state.json; failures are retried next run."""
    watched = cfg.get("auto_backorder_domains", [])
    if not watched:
        return
    state_dir = Path(state_dir)
    state_path = state_dir / "auto_backorder_state.json"
    placed = load_placed(state_path)
    present = {domain for domain, _parked, _release in rows}

    for domain in watched:
        if domain in placed or domain not in present:
            continue
        result = register_backorder(
            domain,
            cfg,
            dry_run=cfg["backorder"]["dry_run"],
            log_path=str(state_dir / "dry_run.log"),
        )
        if result.mode == "live":
            log_backorder(state_dir, domain, result)
            result_push(domain, result, prefix="AUTO")
            if result.success:
                mark_placed(state_path, domain, result.order_id)
```

- [ ] **Step 4: Run to verify all auto_backorder tests pass**

Run: `python -m pytest tests/test_auto_backorder.py -v`
Expected: all pass (4 state + 7 orchestration = 11).

- [ ] **Step 5: Commit**

```bash
git add auto_backorder.py tests/test_auto_backorder.py
git commit -m "feat: run_auto_backorders submits watched domains, cap-bypassed"
```

---

## Task 4: Config + gitignore

**Files:**
- Modify: `config.json`
- Modify: `.gitignore`

- [ ] **Step 1: Add the watched-domain array to `config.json`**

Inside the top-level object, after the `backorder` block, add:

```json
  "auto_backorder_domains": ["babakocsi.hu"]
```

(Ensure the preceding `backorder` object keeps a trailing comma so the JSON stays valid. Verify with: `python -c "import json; json.load(open('config.json')); print('ok')"` — expected output `ok`.)

- [ ] **Step 2: Ignore the new server-local state file**

Add a line to `.gitignore`:

```
auto_backorder_state.json
```

- [ ] **Step 3: Commit**

```bash
git add config.json .gitignore
git commit -m "feat: watch babakocsi.hu for auto-backorder; ignore its state file"
```

---

## Task 5: Wire `run_auto_backorders` into `check.py`

**Files:**
- Modify: `check.py` (import, call, skip watched domains in manual notify)
- Test: `tests/test_auto_backorder.py` (add a `check.main()` integration test)

- [ ] **Step 1: Write a failing integration test**

Append to `tests/test_auto_backorder.py` (add `import check` and `from microware_client import RegisterResult` to the imports):

```python
def test_main_auto_backorders_and_skips_manual_notify(tmp_path, cfg, monkeypatch):
    cfg["auto_backorder_domains"] = ["babakocsi.hu"]
    cfg["backorder"]["dry_run"] = False
    cfg["source_url"] = "http://example/parkolas"

    monkeypatch.setattr(check, "load_config", lambda: cfg)
    monkeypatch.setattr(check, "ROOT", tmp_path)
    monkeypatch.setattr(check, "SEEN_PATH", tmp_path / "seen.json")
    (tmp_path / "seen.json").write_text('{"old.hu": "2026-06-01"}', encoding="utf-8")
    monkeypatch.setattr(check, "run_ip_guard", lambda: None)
    monkeypatch.setattr(check, "fetch_domains", lambda url: [
        ("babakocsi.hu", "2026-06-02", "2026-07-03"),
        ("kave.hu", "2026-06-02", "2026-07-03"),
    ])
    # auto path: force a live success without hitting the network
    monkeypatch.setattr(
        "auto_backorder.register_backorder",
        lambda domain, c, **k: RegisterResult(success=True, mode="live", api_code=201, order_id=99),
    )
    notified = []
    monkeypatch.setattr(check, "ntfy_send", lambda headers, body="": notified.append(headers.get("Title", "")))

    rc = check.main()

    assert rc == 0
    placed = load_placed(tmp_path / "auto_backorder_state.json")
    assert placed["babakocsi.hu"]["orderid"] == 99
    # watched domain must NOT get a manual Backorder notification
    assert not any("babakocsi.hu" in title for title in notified)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_auto_backorder.py::test_main_auto_backorders_and_skips_manual_notify -v`
Expected: FAIL — `AttributeError: module 'check' has no attribute 'run_auto_backorders'` (or the watched domain appears in `notified`).

- [ ] **Step 3: Import `run_auto_backorders` in `check.py`**

Add to the imports near `from notify import NTFY_TOPIC, ntfy_send` (around `check.py:29`):

```python
from auto_backorder import run_auto_backorders
```

- [ ] **Step 4: Call it right after the rows are fetched and validated**

In `main()`, immediately after the `print(f"Fetched {len(rows)} domains from source.")` line and before the `seen = load_seen()` / first-run logic, insert:

```python
    run_auto_backorders(cfg, rows, ROOT)
```

(Note: `main()` currently fetches rows AFTER `seen = load_seen()`. Reorder so `fetch_domains` + the empty-rows guard + `run_auto_backorders(cfg, rows, ROOT)` run first, then `seen = load_seen()` / `is_first_run`. The auto path must run regardless of first-run seeding.)

The resulting top of `main()` (after `run_ip_guard()`):

```python
    rows = fetch_domains(cfg["source_url"])
    if not rows:
        print("ERROR: parsed 0 domains — page format may have changed.", file=sys.stderr)
        return 1
    print(f"Fetched {len(rows)} domains from source.")

    run_auto_backorders(cfg, rows, ROOT)

    seen = load_seen()
    today_iso = date.today().isoformat()
    is_first_run = not seen
    # ... existing new-domain / matches loop unchanged ...
```

- [ ] **Step 5: Skip watched domains in the manual per-match ntfy loop**

In the `if matches:` block of `main()`, add a `auto_domains` set and a skip:

```python
    if matches:
        tunnel_url = cfg.get("backorder", {}).get("tunnel_url", "")
        ttl_hours = cfg.get("backorder", {}).get("action_ttl_hours", 24)
        auto_domains = set(cfg.get("auto_backorder_domains", []))
        for domain, _release, reasons in matches:
            if domain in auto_domains:
                continue
            reason_summary = ", ".join(reasons[:2])
            title = f"{domain} - {reason_summary}"
            print(title)
            action_url = build_action_url(
                domain, tunnel_url=tunnel_url, ttl_hours=ttl_hours
            )
            headers = build_ntfy_headers(title=title, action_url=action_url)
            ntfy_send(headers, "")
```

- [ ] **Step 6: Run the integration test, then the full suite**

Run: `python -m pytest tests/test_auto_backorder.py -v`
Expected: all pass (state + orchestration + integration = 12).

Run: `python -m pytest -q`
Expected: entire suite green (no regressions).

- [ ] **Step 7: Commit**

```bash
git add check.py tests/test_auto_backorder.py
git commit -m "feat: check.py auto-backorders watched domains, skips their manual notify"
```

---

## Post-implementation (manual, by the user — NOT part of the coded tasks)

- The `DomainWatch` task already runs the current `check.py`; restarting it is unnecessary because it launches a fresh `python check.py` each run (it does not hold the module in memory like the uvicorn service). No task restart needed.
- Push commits when ready (`git push` in the user's `!` session).
- Update `.remember/remember.md` with the new feature + that `babakocsi.hu` is being watched for auto-backorder.

---

## Self-Review

**1. Spec coverage:**
- Config array → Task 4. ✅
- Persistent `auto_backorder_state.json`, retry-until-success → Task 2 + Task 3. ✅
- Auto-submit from check.py, cap bypass → Task 3 (`run_auto_backorders` never touches `BackorderState`) + Task 5. ✅
- Respects `dry_run`, no mark on dry-run → Task 3 (`test_run_dry_run_does_not_mark_placed`). ✅
- Failure → not placed, retried → Task 3 (`test_run_failure_does_not_mark_placed`). ✅
- Shared `backorder_runner` extraction, "AUTO" prefix → Task 1. ✅
- Skip watched domains in manual notify → Task 5. ✅
- `.gitignore` state file → Task 4. ✅
- Tests → Tasks 1–5. ✅

**2. Placeholder scan:** No TBD/TODO; every code step shows full code; every run step states expected output. ✅

**3. Type consistency:**
- `register_backorder(domain, cfg, *, dry_run, log_path)` — matches `microware_client.register_backorder`. ✅
- `RegisterResult` fields used (`mode`, `success`, `order_id`, `api_code`, `error_number`, `api_message`, `http_status`) — match the dataclass. ✅
- `log_backorder(state_dir, domain, result)` / `result_push(domain, result, prefix="")` — defined in Task 1, called identically in Task 3 and `backorder_api`. ✅
- `load_placed(state_path)` / `mark_placed(state_path, domain, orderid)` / `run_auto_backorders(cfg, rows, state_dir)` — consistent across Tasks 2, 3, 5. ✅
- `rows` element shape `(domain, parked, release)` — matches `fetch_domains` return. ✅
