# Költöztetés saját szerverre — Implementációs terv

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A domain-watch + microware backorder rendszert átvinni a saját szerverre, dinamikus-IP-figyeléssel a microware whitelist miatt, és az LPNTY-t leszerelni.

**Architecture:** Két részből áll. (1) **Kód** — fejleszthető és tesztelhető ezen a gépen (DESKTOP-FTEVD5O), majd push: új `ip_guard.py` IP-figyelő, közös `notify.py` ntfy-helper, 10401-riasztás a `backorder_api.py`-ban, IP-őr beépítése a `check.py`-ba. (2) **Deploy** — a szerveren a felhasználó futtatja: általánosított `register_server_tasks.ps1` + `BOOTSTRAP.md` checklist. A szerver csak `git pull`-lal kapja meg a kódot.

**Tech Stack:** Python 3.12, requests, FastAPI, pytest, cloudflared, Windows Task Scheduler (PowerShell).

---

## Fájlstruktúra

| Fájl | Felelősség | Művelet |
|---|---|---|
| `notify.py` | ntfy push küldés + topic konstans. Külön modul, hogy a `backorder_api` a nehéz `check` (wordfreq) import nélkül tudjon értesíteni. | Create |
| `ip_guard.py` | Publikus IP lekérés + utolsó ismert IP állapot + tiszta `evaluate()` döntés (riasszunk-e). Értesítést NEM küld — azt a hívó teszi. | Create |
| `check.py` | `ntfy_send`/`NTFY_TOPIC` átkerül `notify.py`-ba (re-export a kompatibilitásért), új `run_ip_guard()` minden futáskor. | Modify |
| `backorder_api.py` | A `register_backorder` eredménye `error_number == 10401` esetén ntfy-riasztás az utoljára whitelistelt IP-vel. | Modify |
| `register_server_tasks.ps1` | Paraméterezett task-regisztráló: `DomainWatch` (1 perc), `BackorderAPI`, `CloudflaredTunnel`. | Create |
| `BOOTSTRAP.md` | Lépésről-lépésre szerver-setup + LPNTY-leszerelés + microware whitelist csere. | Create |
| `.gitignore` | `whitelisted_ip.json` hozzáadása. | Modify |
| `tests/test_notify.py`, `tests/test_ip_guard.py`, `tests/test_check_ip_guard.py` | Az új kód tesztjei. | Create |
| `tests/test_backorder_api.py` | +2 teszt a 10401-riasztásra. | Modify |

---

## Task 1: `notify.py` — közös ntfy-helper kiszervezése

**Files:**
- Create: `notify.py`
- Create: `tests/test_notify.py`
- Modify: `check.py:34` (NTFY_TOPIC def), `check.py:245-255` (ntfy_send def)

- [ ] **Step 1: Write the failing test** — `tests/test_notify.py`

```python
"""Tests for the shared ntfy helper."""
from __future__ import annotations

import notify


def test_ntfy_send_posts_to_topic(monkeypatch):
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        return FakeResp()

    monkeypatch.setattr(notify.requests, "post", fake_post)
    notify.ntfy_send({"Title": "hi"}, "body text")

    assert captured["url"].startswith("https://ntfy.sh/")
    assert notify.NTFY_TOPIC in captured["url"]
    assert captured["headers"] == {"Title": "hi"}
    assert captured["data"] == b"body text"


def test_ntfy_send_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(notify.requests, "post", boom)
    notify.ntfy_send({"Title": "hi"}, "")  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_notify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'notify'`

- [ ] **Step 3: Create `notify.py`**

```python
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
```

- [ ] **Step 4: Update `check.py` to import from `notify`**

Delete the `NTFY_TOPIC = "..."` block at `check.py:33-34` and the entire `ntfy_send` function at `check.py:245-255`. In their place, add this import next to the other imports (after `import requests`):

```python
from notify import NTFY_TOPIC, ntfy_send  # noqa: F401 — re-export for send_test_push.py and run_test_notification
```

> Why re-export: `send_test_push.py` calls `check.NTFY_TOPIC` and `check.ntfy_send`; `run_test_notification` in check.py also calls `ntfy_send`. Keeping the names importable from `check` preserves both.

- [ ] **Step 5: Run the full suite to confirm nothing broke**

Run: `python -m pytest -q`
Expected: PASS (all existing tests + the 2 new notify tests)

- [ ] **Step 6: Commit**

```bash
git add notify.py tests/test_notify.py check.py
git commit -m "refactor: extract ntfy_send into shared notify.py"
```

---

## Task 2: `ip_guard.py` — publikus IP figyelő

**Files:**
- Create: `ip_guard.py`
- Create: `tests/test_ip_guard.py`

- [ ] **Step 1: Write the failing test** — `tests/test_ip_guard.py`

```python
"""Tests for the public-IP watchdog."""
from __future__ import annotations

from pathlib import Path

import ip_guard


def test_evaluate_none_when_lookup_failed():
    assert ip_guard.evaluate(None, "1.2.3.4") is None


def test_evaluate_initial_when_no_known_ip():
    action, title, body = ip_guard.evaluate("1.2.3.4", None)
    assert action == "initial"
    assert "1.2.3.4" in body


def test_evaluate_changed_when_ip_differs():
    action, title, body = ip_guard.evaluate("5.6.7.8", "1.2.3.4")
    assert action == "changed"
    assert "1.2.3.4" in body and "5.6.7.8" in body


def test_evaluate_none_when_unchanged():
    assert ip_guard.evaluate("1.2.3.4", "1.2.3.4") is None


def test_save_and_load_round_trip(tmp_path: Path):
    p = tmp_path / "whitelisted_ip.json"
    ip_guard.save_known_ip("9.9.9.9", "2026-05-31T00:00:00+00:00", path=p)
    assert ip_guard.load_known_ip(path=p) == "9.9.9.9"


def test_load_returns_none_when_missing(tmp_path: Path):
    assert ip_guard.load_known_ip(path=tmp_path / "nope.json") is None


def test_current_public_ip_success(monkeypatch):
    class FakeResp:
        text = "203.0.113.7\n"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(ip_guard.requests, "get", lambda *a, **k: FakeResp())
    assert ip_guard.current_public_ip() == "203.0.113.7"


def test_current_public_ip_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(ip_guard.requests, "get", boom)
    assert ip_guard.current_public_ip() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ip_guard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ip_guard'`

- [ ] **Step 3: Create `ip_guard.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ip_guard.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add ip_guard.py tests/test_ip_guard.py
git commit -m "feat: ip_guard public-IP watchdog for microware whitelist"
```

---

## Task 3: IP-őr beépítése a `check.py`-ba

**Files:**
- Modify: `check.py` (import sor `from datetime import ...`, új `run_ip_guard()`, hívás a `main()`-ben)
- Create: `tests/test_check_ip_guard.py`

- [ ] **Step 1: Write the failing test** — `tests/test_check_ip_guard.py`

```python
"""Tests for check.run_ip_guard wiring."""
from __future__ import annotations

import check


def test_run_ip_guard_alerts_and_saves_on_change(monkeypatch):
    sent = {}
    saved = {}
    monkeypatch.setattr(check.ip_guard, "current_public_ip", lambda: "5.6.7.8")
    monkeypatch.setattr(check.ip_guard, "load_known_ip", lambda: "1.2.3.4")
    monkeypatch.setattr(
        check.ip_guard, "save_known_ip",
        lambda ip, ts: saved.update(ip=ip, ts=ts),
    )
    monkeypatch.setattr(
        check, "ntfy_send",
        lambda headers, body="": sent.update(headers=headers, body=body),
    )

    check.run_ip_guard()

    assert "valtozott" in sent["headers"]["Title"]
    assert "5.6.7.8" in sent["body"]
    assert saved["ip"] == "5.6.7.8"


def test_run_ip_guard_silent_when_unchanged(monkeypatch):
    sent = {}
    monkeypatch.setattr(check.ip_guard, "current_public_ip", lambda: "1.2.3.4")
    monkeypatch.setattr(check.ip_guard, "load_known_ip", lambda: "1.2.3.4")
    monkeypatch.setattr(check.ip_guard, "save_known_ip", lambda *a: sent.update(saved=True))
    monkeypatch.setattr(check, "ntfy_send", lambda *a, **k: sent.update(called=True))

    check.run_ip_guard()

    assert sent == {}


def test_run_ip_guard_silent_when_lookup_fails(monkeypatch):
    sent = {}
    monkeypatch.setattr(check.ip_guard, "current_public_ip", lambda: None)
    monkeypatch.setattr(check.ip_guard, "load_known_ip", lambda: "1.2.3.4")
    monkeypatch.setattr(check, "ntfy_send", lambda *a, **k: sent.update(called=True))

    check.run_ip_guard()

    assert sent == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_check_ip_guard.py -v`
Expected: FAIL — `AttributeError: module 'check' has no attribute 'run_ip_guard'` (and `ip_guard` not imported)

- [ ] **Step 3: Update imports in `check.py`**

Change the datetime import line (`check.py:19`) from:

```python
from datetime import date, datetime, timedelta
```

to:

```python
from datetime import date, datetime, timedelta, timezone
```

And add `import ip_guard` next to the other local imports (after `import load_secrets`):

```python
import ip_guard
```

- [ ] **Step 4: Add `run_ip_guard()` to `check.py`**

Add this function just above `def main()`:

```python
def run_ip_guard() -> None:
    """Check the public IP each run; ntfy the user when it changes so they can
    re-whitelist at microware before a live backorder fails with 10401."""
    current = ip_guard.current_public_ip()
    decision = ip_guard.evaluate(current, ip_guard.load_known_ip())
    if decision is None:
        return
    _action, title, body = decision
    ntfy_send({"Title": title}, body)
    ip_guard.save_known_ip(current, datetime.now(timezone.utc).isoformat())
```

- [ ] **Step 5: Call it in `main()`**

In `main()`, right after the `--test` early-return block (`check.py:288-289`) and before `seen = load_seen()`, add:

```python
    run_ip_guard()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_check_ip_guard.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS (everything green)

- [ ] **Step 8: Commit**

```bash
git add check.py tests/test_check_ip_guard.py
git commit -m "feat: run ip_guard each check.py run, alert on IP change"
```

---

## Task 4: 10401-riasztás a `backorder_api.py`-ban

**Files:**
- Modify: `backorder_api.py` (top imports + a `backorder` endpoint result-kezelése)
- Modify: `tests/test_backorder_api.py` (+2 teszt)

- [ ] **Step 1: Write the failing tests** — add to `tests/test_backorder_api.py`

Add these imports at the top of the file (next to the existing imports):

```python
import backorder_api
from microware_client import RegisterResult
```

Append these two tests at the end of the file:

```python
def test_10401_triggers_ntfy_alert(tmp_path, cfg, monkeypatch):
    cfg["backorder"]["dry_run"] = False
    cfg_path = write_cfg(tmp_path, cfg)

    monkeypatch.setattr(
        backorder_api, "register_backorder",
        lambda domain, c, **k: RegisterResult(
            success=False, mode="live", error_number=10401,
            api_message="10401: Authentication failed",
        ),
    )
    alerts = []
    monkeypatch.setattr(
        backorder_api, "ntfy_send",
        lambda headers, body="": alerts.append((headers, body)),
    )
    monkeypatch.setattr(backorder_api.ip_guard, "load_known_ip", lambda: "1.2.3.4")

    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)
    exp = int(time.time()) + 3600
    r = client.post(
        "/backorder",
        params={"domain": "foo.hu", "exp": exp, "sig": sign("foo.hu", exp)},
    )
    assert r.status_code == 200, r.text
    assert len(alerts) == 1
    assert "1.2.3.4" in alerts[0][1]
    assert "10401" in alerts[0][1]


def test_successful_live_result_does_not_alert(tmp_path, cfg, monkeypatch):
    cfg["backorder"]["dry_run"] = False
    cfg_path = write_cfg(tmp_path, cfg)

    monkeypatch.setattr(
        backorder_api, "register_backorder",
        lambda domain, c, **k: RegisterResult(
            success=True, mode="live", api_code=201, order_id=42,
        ),
    )
    alerts = []
    monkeypatch.setattr(
        backorder_api, "ntfy_send",
        lambda headers, body="": alerts.append((headers, body)),
    )

    app = create_app(cfg_path=cfg_path, state_dir=tmp_path)
    client = TestClient(app)
    exp = int(time.time()) + 3600
    r = client.post(
        "/backorder",
        params={"domain": "bar.hu", "exp": exp, "sig": sign("bar.hu", exp)},
    )
    assert r.status_code == 200, r.text
    assert alerts == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_backorder_api.py::test_10401_triggers_ntfy_alert -v`
Expected: FAIL — `AttributeError: module 'backorder_api' has no attribute 'ntfy_send'`

- [ ] **Step 3: Add imports to `backorder_api.py`**

Add next to the existing module imports (after `from microware_client import register_backorder`):

```python
import ip_guard
from notify import ntfy_send
```

- [ ] **Step 4: Add the 10401 alert in the endpoint**

In `backorder_api.py`, in the `backorder` endpoint, after the `result = register_backorder(...)` call and before the `return {...}` block, insert:

```python
        if result.error_number == 10401:
            known = ip_guard.load_known_ip()
            ntfy_send(
                {"Title": "domain-watch: backorder 10401"},
                f"Backorder ELHALT ({domain}): 10401 auth hiba. Utoljara "
                f"whitelistelt IP: {known or 'ismeretlen'}. Ellenorizd a "
                "szerver publikus IP-jet es a microware username-et.",
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_backorder_api.py -v`
Expected: PASS (existing 6 + 2 new)

- [ ] **Step 6: Commit**

```bash
git add backorder_api.py tests/test_backorder_api.py
git commit -m "feat: ntfy alert on backorder 10401 with last whitelisted IP"
```

---

## Task 5: `register_server_tasks.ps1` — paraméterezett task-regisztráló

**Files:**
- Create: `register_server_tasks.ps1`

> Nincs unit-teszt (Windows Task Scheduler). Verifikáció: a script végi `Get-ScheduledTaskInfo` állapotkiírás a szerveren.

- [ ] **Step 1: Create `register_server_tasks.ps1`**

```powershell
# Register Task Scheduler entries on the user's own server:
#   - DomainWatch:      runs check.py every 1 minute
#   - BackorderAPI:     runs uvicorn for backorder_api at boot
#   - CloudflaredTunnel: runs cloudflared named tunnel at boot
#
# All run via S4U logon (no stored password) as the current user by default.
# Idempotent: unregisters before registering.
#
# Discover the values to pass FIRST (on the server):
#   (Get-Command python).Source            -> -PyExe
#   whoami /user                           -> -AdminSid (the SID column)
# Example:
#   .\register_server_tasks.ps1 -PyExe "C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe" -DwDir "C:\domain-watch"

param(
    [string]$PyExe    = "C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe",
    [string]$CfExe    = "C:\Program Files (x86)\cloudflared\cloudflared.exe",
    [string]$CfConfig = "$env:USERPROFILE\.cloudflared\config.yml",
    [string]$DwDir    = "C:\domain-watch",
    [string]$TunnelName = "domain-watch-backorder",
    [string]$AdminSid
)

$ErrorActionPreference = "Stop"

if (-not $AdminSid) {
    $AdminSid = ([System.Security.Principal.WindowsIdentity]::GetCurrent()).User.Value
    Write-Host "Using current user SID: $AdminSid"
}

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 3 `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$principal = New-ScheduledTaskPrincipal -UserId $AdminSid -LogonType S4U -RunLevel Highest

function Register-DwTask {
    param([string]$Name, [string]$Exe, [string]$ArgList, [string]$WorkingDir, $Trigger)
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    $action = New-ScheduledTaskAction -Execute $Exe -Argument $ArgList -WorkingDirectory $WorkingDir
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger -Principal $principal -Settings $settings | Out-Null
    Write-Host "Registered: $Name"
}

$atStartup = New-ScheduledTaskTrigger -AtStartup
$everyMinute = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

Register-DwTask -Name "DomainWatch" -Exe $PyExe -ArgList "check.py" -WorkingDir $DwDir -Trigger $everyMinute
Register-DwTask -Name "CloudflaredTunnel" -Exe $CfExe -ArgList "--config `"$CfConfig`" tunnel run $TunnelName" -WorkingDir "C:\" -Trigger $atStartup
Register-DwTask -Name "BackorderAPI" -Exe $PyExe -ArgList "-m uvicorn backorder_api:app --port 8000" -WorkingDir $DwDir -Trigger $atStartup

Write-Host ""
Write-Host "Starting boot tasks..."
Start-ScheduledTask -TaskName "CloudflaredTunnel"
Start-ScheduledTask -TaskName "BackorderAPI"
Start-ScheduledTask -TaskName "DomainWatch"
Start-Sleep -Seconds 8

Write-Host ""
Write-Host "Status:"
Get-ScheduledTask -TaskName "DomainWatch", "CloudflaredTunnel", "BackorderAPI" |
    ForEach-Object {
        $info = $_ | Get-ScheduledTaskInfo
        "{0,-18} state={1,-9} lastRun={2} lastResult={3}" -f $_.TaskName, $_.State, $info.LastRunTime, $info.LastTaskResult
    }
```

- [ ] **Step 2: Commit**

```bash
git add register_server_tasks.ps1
git commit -m "chore: parameterized register_server_tasks.ps1 (DomainWatch + API + tunnel)"
```

---

## Task 6: `BOOTSTRAP.md` + `.gitignore`

**Files:**
- Create: `BOOTSTRAP.md`
- Modify: `.gitignore`

- [ ] **Step 1: Add `whitelisted_ip.json` to `.gitignore`**

Append this line to `.gitignore` (after `submitted_today.json`):

```
whitelisted_ip.json
```

- [ ] **Step 2: Create `BOOTSTRAP.md`**

```markdown
# Szerver bootstrap — domain-watch + backorder

Ezt a checklist-et a **saját szerveren** (hostname `szerver`) futtasd, RDP-ről.
A `<...>` helyőrzőket a saját értékeiddel töltsd ki.

## 1. Kód + függőségek

```powershell
cd C:\
git clone https://github.com/JollyLlama-code/domain-watch.git
cd C:\domain-watch
pip install -r requirements.txt
Remove-Item seen.json -ErrorAction SilentlyContinue   # TISZTA START: az elso futas csendben seedel, nincs ertesites-cunami
```

> A repóban commitolt `seen.json` ~11k régi bejegyzést tartalmaz; ezt
> kötelező törölni, különben az első futás nem seed-skippel. A `check.py`
> az első futáskor újra létrehozza (üresből seedelve). NE commitold vissza
> a törlést — csak helyben, a szerveren.

> Ha a `C:\` gyökérbe írás elakad (Defender/NTFS védelem), klónozz a
> `$env:USERPROFILE`-ba és add meg a `-DwDir`-t a task-scriptnek.

## 2. Titkok (NEM a repóból — frissen)

Hozd létre `C:\domain-watch\secrets.env`:

```
MICROWARE_API_PASSWORD=<a microware API jelszo, NEM a login jelszo>
BACKORDER_HMAC_SECRET=<uj veletlen 32+ hex; generald: python -c "import secrets;print(secrets.token_hex(32))">
```

> A `gittoken.txt`-t és a régi `secrets.env`-t NE másold át az LPNTY-ről.

## 3. cloudflared tunnel

```powershell
cloudflared tunnel login                       # böngészőben hagyd jóvá lappantyu.com-ra
cloudflared tunnel create domain-watch-backorder
python make_cf_config.py                        # config.yml -> backorder.lappantyu.com:8000
cloudflared tunnel route dns domain-watch-backorder backorder.lappantyu.com
```

> A Cloudflare DNS-ben a `backorder.lappantyu.com` CNAME most az új tunnelre
> mutat. Ha a régi LPNTY route ütközne, töröld azt (lásd 7. lépés).

## 4. Taskok regisztrálása

Derítsd ki az értékeket, majd futtasd:

```powershell
(Get-Command python).Source     # ezt add -PyExe-nek
whoami /user                     # a SID oszlopot add -AdminSid-nek (vagy hagyd ki: a current user lesz)

.\register_server_tasks.ps1 -PyExe "<python.exe utvonal>" -DwDir "C:\domain-watch"
```

Várt: mindhárom task `state=Ready/Running`, `lastResult=0` vagy `267009` (fut).

## 5. Smoke a tunnelen

```powershell
python smoke_lpnty.py https://backorder.lappantyu.com
```

Ezután állítsd be a `config.json`-ban:
- `backorder.tunnel_url` = `https://backorder.lappantyu.com/backorder`
- `backorder.enabled` = `true`, `backorder.dry_run` = `true` (még NEM élesítünk)

## 6. Microware whitelist csere

`admin.microware.hu` → Beállítások → API hozzáférés beállítása:
- Vedd fel a szerver aktuális publikus IP-jét (az IP-őr első ntfy push-a
  megmondja, vagy: `Invoke-WebRequest api.ipify.org`).
- Töröld a régi `178.48.104.118` (LPNTY) IP-t.

## 7. LPNTY leszerelés (a haver gépén)

```powershell
Unregister-ScheduledTask -TaskName "DomainWatch","BackorderAPI","CloudflaredTunnel" -Confirm:$false
cloudflared tunnel route dns --overwrite-dns domain-watch-backorder backorder.lappantyu.com   # ha a régi route maradt volna
```

> Ha a régi tunnelt teljesen meg akarod szüntetni: `cloudflared tunnel delete <regi-tunnel-id>` az LPNTY-n.

## 8. Dry-run éles smoke (nincs költés)

A telefonon kapj egy match push-t (vagy `python send_test_push.py`), nyomd meg
a **Backorder** gombot. A `dry_run.log`-ban megjelenik a teljes microware
request body, valós hívás és költés nélkül. Ha eddig minden zöld, a költözés
kész.

## Élesítés (KÉSŐBB, külön döntés)

`dry_run:false` + alacsony értékű első célpont — ez NEM része a költözésnek.
```

- [ ] **Step 3: Commit**

```bash
git add BOOTSTRAP.md .gitignore
git commit -m "docs: BOOTSTRAP.md server setup + gitignore whitelisted_ip.json"
```

---

## Záró ellenőrzés (ezen a gépen)

- [ ] **Teljes suite zöld:** `python -m pytest -q` → minden PASS.
- [ ] **Push:** a kód- és doc-commitok felmennek (csak explicit megerősítés után — a felhasználó preferenciája szerint a push külön jóváhagyást igényel).
- [ ] **Szerver-oldali lépések:** a `BOOTSTRAP.md` szerint a felhasználó futtatja RDP-n; az LPNTY leszerelés a 7. lépésben.

## Megjegyzés a végrehajtásról

Az 1–4. és 6. task (kód + doc) ezen a gépen (DESKTOP-FTEVD5O) fejleszthető és
tesztelhető, mert ugyanaz a repo. Az 5. task scriptje is itt készül, de csak a
szerveren fut. A tényleges deploy (cloudflared, taskok, whitelist, LPNTY-
leszerelés) a `BOOTSTRAP.md` szerint a szerveren történik.
