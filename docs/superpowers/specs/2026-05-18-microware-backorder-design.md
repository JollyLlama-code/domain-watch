# Microware backorder integration — design

**Status:** locked, ready for implementation
**Date:** 2026-05-18
**Author:** session-driven, based on user decisions in `project_microware-backorder` memory and 2026-05-18 follow-up

## Background

Watcher (`check.py` on LPNTY) detects `.hu` domains in pre-deletion parking that match scoring rules. Today: one batched ntfy push lists matches; user must manually order via the registrar portal — too slow against competitors.

Goal: one-tap backorder from the phone notification. User taps **Backorder** in ntfy → microware API `/domains/register` is called → if competitor doesn't beat us, microware catches the domain at 2604 Ft.

## Constraints

- Real money: 2604 Ft per successful catch. No sandbox for backorder (microware confirmed 2026-05-18) — only `dry_run` mode locally and a cautious first production catch.
- Microware API only accepts requests from whitelisted IPs (max 6). LPNTY's `178.48.104.118` is the IP. So the HTTP call originates from LPNTY.
- ntfy `http` action triggers a POST from the ntfy server to a public URL. That URL must reach LPNTY. → cloudflared tunnel exposes a stable HTTPS URL pointing at LPNTY:8000 (deferred — friend's setup).
- Topic `domwatch-m5dcuxgprlov6zea90i1` is public; the action URL is visible to anyone subscribed. → URL must be HMAC-signed so attackers can't forge backorder requests for arbitrary domains.

## Architecture

```
+------------------+    +------------------+    +-----------------+
|  check.py        |    |  ntfy.sh         |    |  user's phone   |
|  on LPNTY        |---->| topic            |---->| (subscribed)    |
|  every 1 min     |    | + Actions header |    | shows push      |
+------------------+    +------------------+    +--------+--------+
                                                         | taps "Backorder"
                                                         v
+------------------+    +------------------+    +-----------------+
|  microware       |<---|  backorder_api   |<---|  ntfy server    |
|  /domains/       |    |  FastAPI on      |    |  POSTs http     |
|  register        |    |  LPNTY:8000      |    |  action URL     |
|  (Basic auth)    |    |  via cloudflared |    +-----------------+
+------------------+    +------------------+
```

Three new modules + modifications to existing files:

| File | Purpose |
|---|---|
| `microware_client.py` | HTTP Basic auth, request body construction, response/error mapping. Pure function: `register_backorder(domain, cfg, *, dry_run) -> RegisterResult`. |
| `backorder_state.py` | JSON-backed daily counter + per-domain dedup. `attempt_submit(domain)` returns `(allowed, reason)`. Resets at midnight Europe/Budapest. |
| `backorder_api.py` | FastAPI app exposing `POST /backorder?domain=X&exp=TS&sig=HEX`. Verifies HMAC, checks state, calls microware client, returns JSON. |
| `check.py` | Modified: per-match ntfy push (was: one batched push) with `Actions: http` header containing HMAC-signed URL. |
| `config.json` | Adds `microware` and `backorder` sections. |
| `requirements.txt` | Adds `fastapi`, `uvicorn[standard]`, `pydantic`, `pytest`, `httpx`. |

## Key decisions

### Authentication of action URL — HMAC SHA-256

URL shape:
```
https://<tunnel>.cfargotunnel.com/backorder?domain=foo.hu&exp=1747600000&sig=<32hex>
```

`sig = hex(HMAC_SHA256(secret, f"{domain}|{exp}"))[:32]`

- `secret` lives in `secrets.env` on LPNTY only, never in git
- `exp` = Unix timestamp 24h after notification; FastAPI rejects expired URLs
- Truncating to 32 hex (128 bits) is plenty for this threat model
- `check.py` signs at notification time, FastAPI verifies on receipt

### State files (`backorder_state.py`)

- `daily_count.json` — `{"date": "2026-05-18", "count": 3}`. Resets when date changes.
- `submitted_today.json` — `{"foo.hu": "2026-05-18T15:23:11Z", ...}`. Pruned on date change.

Both stored next to `check.py` (LPNTY: `C:\domain-watch\`). Atomic write via temp-file rename to survive concurrent writes (FastAPI single-process; unlikely but cheap).

### Cap enforcement (10/day)

`attempt_submit(domain)`:
1. If `domain in submitted_today` → return `(allowed=False, reason="already_submitted_today")` (HTTP 200, idempotent — re-tap doesn't error)
2. If `count >= cap` → return `(allowed=False, reason="daily_cap_reached")` (HTTP 429)
3. Else: increment count, record domain, write both files, return `(allowed=True, ...)`

Order matters: dedup check before cap check, so re-taps don't consume cap.

### dry_run flow

When `cfg.backorder.dry_run is True`:
1. `register_backorder` builds the full request body
2. Logs it as JSON to `dry_run.log`
3. Returns a synthetic `RegisterResult(success=True, mode="dry_run", body=...)` — NO HTTP call

This lets us test the full check.py → ntfy → FastAPI → microware_client chain end-to-end against production microware **without spending money**.

### `enabled` flag

`cfg.backorder.enabled` defaults `false`. When false, FastAPI returns 503 to all `/backorder` calls. Manual flip to `true` is the production gate.

This means the implementation can be fully deployed and tested with `enabled=false`, then a single config edit + restart flips production on.

### Microware request body for .hu

Per API docs page 23 + error 10209 + error 10335:
```python
{
    "domain": "foo.hu",
    "years": 2,                          # .hu minimum
    "ns1": "ns1.microware.hu",           # config — verify in microware portal
    "ns2": "ns2.microware.hu",           # config
    "owner": cfg.microware.owner_contact_id,
    "type": "1f",                        # .hu requires 1f or 2f; 1f for non-2FA accounts
    "declarations": HU_DECLARATION_TEXT, # constant copied from API docs page 25
}
```

`HU_DECLARATION_TEXT` is hardcoded in `microware_client.py` since it's a fixed registry-required string (not user-configurable).

### Per-match notification format

Replaces the current batched ntfy push. For each new match `check.py` POSTs:

```
Title: foo.hu — en word
Body:  (empty — title carries everything)
Actions: http, Backorder, https://<tunnel>/backorder?domain=foo.hu&exp=...&sig=..., method=POST, clear=true
```

`clear=true` removes the notification after the user taps it, so the queue stays clean.

If `cfg.backorder.tunnel_url` is empty (LPNTY/tunnel not set up yet), check.py falls back to **no action button** — just sends the per-match title as a plain push. This means the per-match notification refactor can ship and run on LPNTY before the tunnel exists.

### Source-side filtering — explicitly NOT added

Per [[feedback_one-push-per-match]]: user wants the raw firehose. No filtering at notification time. The brake is the 10/day cap on the API side, not "only notify on high signal."

## Config additions

```json
{
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

Secrets live in `secrets.env` (gitignored), not config:
- `MICROWARE_API_PASSWORD` — set on microware portal → "API hozzáférés beállítása"
- `BACKORDER_HMAC_SECRET` — generated once with `python -c "import secrets; print(secrets.token_hex(32))"`, shared by check.py and FastAPI

## What ships in this session vs deferred

**This session (no LPNTY needed):**
1. `microware_client.py` + tests
2. `backorder_state.py` + tests
3. `backorder_api.py` + tests (run via `uvicorn` locally for smoke)
4. `check.py` per-match refactor + tunnel_url-empty fallback + tests
5. Config schema + example `secrets.env.example`
6. Updated `requirements.txt` + `.gitignore`

**Deferred (needs friend / LPNTY):**
1. Whitelist `178.48.104.118` in microware portal (user can do solo via portal)
2. Generate `MICROWARE_API_PASSWORD` (user solo via portal)
3. Set up cloudflared tunnel on LPNTY pointing at `localhost:8000`
4. Install new deps + place `secrets.env` on LPNTY
5. Register `BackorderAPI` Task Scheduler entry (or run as service)
6. Pull updated code on LPNTY, smoke test in dry_run mode against production microware
7. Final cutover: flip `enabled: true`, pick a deliberately low-value first target, verify charge + ownership

## Open questions (won't block this session)

- Default microware NS hostnames — assumed `ns1.microware.hu` / `ns2.microware.hu` (common pattern); user verifies in portal before production cutover. Config-overridable.
- Whether to use `1f` or `2f` for `type` field — assumed `1f` (no two-factor for an account-level setting on individual domain). User verifies.
- Whether microware sandbox emails come back saying "actually here's a different test endpoint" — if so, swap `base_url` in config; no code change.
