# Auto-backorder for a watched domain — design

Date: 2026-06-02
Status: Approved (design), pending implementation plan

## Goal

Guarantee that a specific, user-chosen `.hu` domain (initially `babakocsi.hu`)
is backordered automatically the moment it appears on the pre-deletion
parking list in a backorderable state — without a manual ntfy tap, and
without being blocked by the daily safety cap.

`babakocsi.hu` is not on the parking list yet; the user expects it to be
released later. The system must keep watching and place the backorder on its
own when it shows up.

## Decisions (from brainstorming)

1. **Fully automatic submit** — no manual tap. Real money: 2604 Ft only on a
   successful catch at the drop.
2. **Scope: just `babakocsi.hu`** — no watchlist-management machinery. The
   domain lives in a config array so adding another later is a one-line edit,
   but we build nothing beyond reading that array.
3. **Bypasses the daily cap** — a watched-domain auto-submit always goes
   through regardless of how many manual backorders happened that day, and
   does not consume a cap slot. Rationale: it is a deliberately chosen single
   domain, not a noisy filter match.
4. **Retry until caught, then stop** — attempt every run until a live `201`
   success is recorded; after that, never resubmit.
5. **Respects `dry_run`** — under `dry_run:true`, log to `dry_run.log` and do
   NOT mark the domain as placed (so it submits for real once live).

## Architecture (Approach A: submit directly from `check.py`)

`check.py` already scrapes the parking list and runs on the scheduled window
(00:00–00:15 daily). It gains a post-scrape step that handles watched domains
directly, reusing `microware_client` (the `.backorder` + `years=1` body is
already baked in) and `notify`. No HMAC/tunnel indirection — this is an
internal action on the same server. The `backorder_api` ntfy-tap path is
unchanged.

### Components

**config.json**
```json
"auto_backorder_domains": ["babakocsi.hu"]
```
Plain data array. Empty/missing → feature is a no-op.

**`auto_backorder_state.json`** (server-local, gitignored, persistent — NOT
daily-reset)
```json
{ "babakocsi.hu": { "orderid": 123456, "ts": "2026-06-02T00:07:11+00:00" } }
```
Records domains we have **successfully** backordered. Distinct from
`submitted_today.json` (daily cap dedup). A domain absent here is retried
every run; once present, it is never resubmitted. This gives
"retry-until-caught, then stop".

**`auto_backorder.py`** (new module)
- `load_placed(state_path) -> dict` — read state, `{}` if missing/corrupt.
- `mark_placed(state_path, domain, orderid)` — atomic write (temp + rename),
  mirroring `backorder_state.py`'s style.
- `run_auto_backorders(cfg, rows, state_dir)` — orchestration:
  - `watched = cfg.get("auto_backorder_domains", [])`; return if empty.
  - `present = {domain for domain, _parked, _release in rows}`.
  - `placed = load_placed(state_dir / "auto_backorder_state.json")`.
  - For each `d` in `watched`:
    - skip if `d in placed` (already caught/placed).
    - skip if `d not in present` (not on the list yet — keep watching).
    - otherwise submit:
      `result = register_backorder(d, cfg, dry_run=cfg["backorder"]["dry_run"], log_path=str(state_dir / "dry_run.log"))`
      (Note: cap is bypassed — `BackorderState` is NOT consulted.)
    - if `result.mode == "live"`:
      - `log_backorder(state_dir, d, result)`
      - `result_push(d, result, prefix="AUTO")`
      - if `result.success`: `mark_placed(..., d, result.order_id)`
    - if `dry_run`: nothing is marked placed (re-submits for real once live).

**`backorder_runner.py`** (new shared module — small refactor)
- `log_backorder(state_dir, domain, result)` — moved verbatim from
  `backorder_api.py`.
- `result_push(domain, result, prefix="")` — moved from `backorder_api.py`,
  with an optional title prefix so the auto path can tag pushes "AUTO".
- `backorder_api.py` imports both from here instead of defining them locally.
  The ntfy-tap behaviour is otherwise unchanged.

**`check.py`**
- Call `run_auto_backorders(cfg, rows, state_dir)` right after `rows` is
  fetched and validated, **before** the `is_first_run` early return — so it
  runs independently of `seen.json` state (the watched domain must be handled
  even on a seeding run, and retried on later days when it is no longer
  "new"). The auto path uses the full scraped `rows` (all domains currently
  on the list), not only newly-seen ones.
- In the per-match ntfy loop, **skip** any domain in `auto_backorder_domains`
  (no redundant manual Backorder button for a domain handled automatically;
  the auto path sends its own result push).

**`.gitignore`** — add `auto_backorder_state.json`.

## Data flow (happy path)

1. 00:07, the daily refresh lists `babakocsi.hu` (revoked/backorderable).
2. `check.py` run in the window scrapes it; `run_auto_backorders` sees it is
   watched, not yet placed, and present on the list.
3. `register_backorder("babakocsi.hu", ...)` POSTs
   `domain=babakocsi.hu.backorder, years=1` over IPv4 → `201`, orderid N.
4. `log_backorder` appends to `backorder.log`; `result_push` sends
   "AUTO babakocsi.hu — ELKAPVA, order N"; `mark_placed` records it.
5. Subsequent runs see it in `auto_backorder_state` → skip. microware catches
   it at the drop; charge only then.

## Error handling

- **10256 (not yet revoked / "felmondás")** or transient network/API errors:
  result is logged + an ❌ push sent, nothing marked placed → retried next run
  and next day. No charge on failure.
- **10401 (auth/IP)**: existing 🔑 push via `result_push` (re-whitelist IP).
- **dry_run**: synthetic success to `dry_run.log`, not marked placed.
- Corrupt/missing `auto_backorder_state.json`: treated as empty (retry).
- Repeated 10256 within a 16-run window is harmless (free, no charge).

## Testing (`tests/test_auto_backorder.py`)

Mock the API with `responses` (as existing tests do):
- state `load_placed`/`mark_placed`: atomic write, persists across loads,
  survives missing/corrupt file.
- on list + not placed + live 201 → submits, marks placed (orderid stored),
  logs to backorder.log, sends push.
- already placed → does NOT resubmit (no HTTP call).
- not on the list → does NOT submit.
- `dry_run:true` → does NOT mark placed; no live POST.
- live failure (e.g. 10256) → not marked placed; retried on next call.
- cap bypass → `BackorderState` files are never touched by the auto path.
- empty/missing `auto_backorder_domains` → no-op.

Also confirm `backorder_api` still passes after the `log_backorder` /
`result_push` extraction (import-path change only).

## Files touched

- `config.json` — add `auto_backorder_domains`.
- `auto_backorder.py` — new (state + orchestration).
- `backorder_runner.py` — new (extracted `log_backorder` + `result_push`).
- `backorder_api.py` — import from `backorder_runner`.
- `check.py` — call `run_auto_backorders`; skip auto-domains in manual notify.
- `.gitignore` — add `auto_backorder_state.json`.
- `tests/test_auto_backorder.py` — new.

## Out of scope (YAGNI)

- Watchlist management UI/commands.
- Cancellation of an auto-placed backorder (done manually via portal/support,
  as established for curry.hu).
- Per-domain custom NS/owner/years overrides — all use the existing
  `microware` config block.
