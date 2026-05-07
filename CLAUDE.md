# domain-watch — context for future Claude sessions

## What this is

Daily watcher for high-value `.hu` domains about to free up. Source page:
https://info.domain.hu/parkolas/hu/ido.html (HTML table, ~9.5k rows,
domains released 31 days after entering pre-deletion parking).

User is a `.hu` domain investor. Goal: get a Telegram ping when a
high-value domain enters the parking list, so it can be backordered
through a `.hu` accredited registrar before competitors grab it.

## Architecture

| File | Purpose |
|---|---|
| `check.py` | Scrape, score, dedupe via `seen.json`, send Telegram. Has `--test` flag for sample notifications. |
| `config.json` | Tunables (length thresholds, zipf cutoffs, keyword list, etc.) |
| `seen.json` | State — domains we've already notified about. Committed back by the workflow. Pruned to last 90 days. |
| `preview.py` | Dry-run scan of today's page, no state, no Telegram. Saves full match list to `today_matches.txt` (gitignored). |
| `.github/workflows/check.yml` | Triggered every minute via `repository_dispatch` (external cron-job.org), with `schedule` and `workflow_dispatch` as fallbacks. Commits updated `seen.json`. |
| `requirements.txt` | requests, beautifulsoup4, wordfreq |

Repo: `JollyLlama-code/domain-watch` (**public** — required for unlimited GHA minutes at 1-min cadence). Runs on GitHub Actions.

## Triggering / scheduling

GitHub's own cron scheduler turned out to be completely unreliable for this
repo — zero scheduled runs ever fired in 24h+ across multiple cron syntaxes,
the disable/re-enable trick, and a public-visibility transition. This is a
well-known GHA bug for newly-created personal-account repos.

**Current setup (2026-05-07):**

1. **cron-job.org** (free account, owned by user) hits the GitHub
   `repository_dispatch` API every 1 minute.
2. The workflow listens on `repository_dispatch: types: [poll]` and runs.
3. A `concurrency: domain-watch` group serializes runs so the `seen.json`
   commits don't collide if a run takes longer than 1 minute.
4. The native `schedule: */5 * * * *` is kept in the YAML as a backup in
   case GitHub's scheduler ever wakes up — but in practice it does nothing.

**The dispatch token (GitHub PAT)** lives only in cron-job.org's job config:
- Fine-grained PAT, scoped to *only* `JollyLlama-code/domain-watch`
- Permission: `Contents: Read and write` (the minimum for `repository_dispatch`)
- Expiry: 2027-05-06 — needs renewal before then
- Token name: `domain-watch-cron`
- Never store this token in the repo, in chat, or anywhere logged

**cron-job.org request config** (for reference if it has to be rebuilt):
- URL: `https://api.github.com/repos/JollyLlama-code/domain-watch/dispatches`
- Method: POST
- Headers: `Accept: application/vnd.github+json`, `Authorization: Bearer <PAT>`,
  `Content-Type: application/json`, `User-Agent: domain-watch-cron`,
  `X-GitHub-Api-Version: 2022-11-28`
- Body: `{"event_type":"poll"}`
- Schedule: every 1 minute

If the PAT is ever leaked or compromised: revoke at
https://github.com/settings/personal-access-tokens, create a new one with
the same scope/permissions, and update only the `Authorization` header
value in cron-job.org. No code change needed.

## How scoring works (`score()` in check.py)

A domain matches if ANY of these fire (controlled by `notify_on` in config):

- **short** — label length ≤ `max_short_length` (currently 4)
- **dictionary** — single word in EN or HU wordfreq, zipf ≥ `min_word_zipf_frequency` (3.0). Skips digits, hyphenated, accented Hungarian via deaccent fallback table.
- **compound** — splits cleanly into 2 dictionary parts. Stricter thresholds (`compound_min_part_zipf` 4.0, `compound_min_part_length` 4) to cut the 3+3 noise. Cross-language allowed — tags as `en+hu compound` if mixed.
- **keywords** — substring match against user-curated `keywords` array. Empty by default. User can populate with niches like `["bor", "auto", "etterem"]`.
- **all_numeric** — label is digits only.

`xn--` (IDN) labels are excluded via `ignore_substrings`.

## Hungarian accent handling

URLs are ASCII, but the Hungarian wordlist has accented forms. So `kavehaz`
won't match `kávéház` and `nyari` won't match `nyári`. Workaround:
`_hu_deaccent_zipf_table()` builds a `{deaccented: max_zipf}` lookup from
top 80k HU words at startup (~1s). `_zipf()` falls back to this when
direct lookup is 0 and lang is `hu`. Both `is_dictionary_word` and
`is_compound_word` use it.

## State semantics

- `seen.json` keys = domain, value = ISO date first seen.
- First run (empty `seen.json`) seeds silently — no notification flood.
- Workflow commits `seen.json` back via `github-actions[bot]` with `[skip ci]`.
- 90-day retention prunes old entries since the source page only shows
  domains parked in the last ~31 days.

## Secrets (GitHub Actions)

- `TELEGRAM_BOT_TOKEN` — `@Doma1n_bot` (created via @BotFather)
- `TELEGRAM_CHAT_ID` — user's Telegram user ID (DMs to bot use user ID as chat ID)

User: jollylama06@gmail.com. Bot: t.me/Doma1n_bot.

## Local commands

```powershell
cd C:\Users\User\Documents\domain-watch
python preview.py        # dry-run scan, no state, no notify
python check.py --test   # sample notification with today's top dict matches
python check.py          # real run (requires env vars or skips notify)
```

## Known limitations / things that miss

Discussed with user; rejected as too noisy or out of scope:

1. **Hungarian inflection** — wordfreq has base forms only. `kertem`, `mobillal`, `házak` don't match. Would need a HU lemmatizer (e.g. emMorph) — heavy dependency.
2. **Brand / proper nouns** — `tesla.hu`, `nintendo.hu` not in dict. Rare in expiring lists.
3. **Pronounceable pseudo-words** (CVCV like `kelora`, `vibora`) — would need a phonotactic check; adds noise.
4. **Numeric pattern quality** — `7777` and `2508` get the same tag. Could add repdigit/palindrome bonuses.
5. **Hyphenated dictionary words** — currently fully rejected to avoid false positives from wordfreq tokenization.

## Active TODO: backorder automation

User asked about auto-registering matched domains via `domdom.hu` or
`microware.hu`. Both registrars replied to user's email inquiry:

### microware.hu — VIABLE PATH (chosen direction)

Reply from Hajdu István (domreg@microware.hu, +36-1-432-3236):

- ✅ API supports backorder ("visszavont domain") submission
- ✅ Available to any regular customer — no reseller agreement needed
- ✅ Where: `admin.microware.hu` → Beállítások → API hozzáférés beállítása
- ✅ Pricing: 2 050 Ft + VAT (~2 604 Ft) per *successful* catch only. No monthly fee. No charge on failure (refund/balance restore).
- 📚 Knowledgebase (login required): https://admin.microware.hu/knowledgebase.php

### domdom.hu — NO API

Reply from Túri Gábor (domdom.hu):

- ❌ No API. Web-based ordering only.
- Same pricing model as microware (no fee on failure).
- ⚠️ Mentioned: *"Nem kizárt, hogy már év végén megszűnik a .hu-nál az elkapás lehetősége"* — possible but unconfirmed end-of-2026 discontinuation.

### .hu drop-catching policy status (researched 2026-05-06)

- No public announcement found about ending drop-catching at nic.hu / ISZT / domain.hu.
- 2025 NIS2-driven policy update (March 2025) modernized the registry but did NOT remove drop-catching. Pre-deletion parking still exists.
- domain.hu mentions a **60-day** pre-deletion parking period in current policy — **our scraper assumes 31 days based on the source page text**. Worth verifying this is still accurate; the 31-day figure may be outdated.
- Túri's hint about year-end discontinuation: most likely insider rumor about a possible next-phase change, not a confirmed shutdown. Worth monitoring https://www.domain.hu/tudastar/megujul-a-hu-domainregisztracio/

### Build plan (when user resumes)

1. **User action first** — register at admin.microware.hu, enable API access, generate API key
2. Add `MICROWARE_API_KEY` as GitHub Actions secret (do NOT paste the key into chat — chat is logged)
3. User pulls the API docs from the knowledgebase (login required) and shares the backorder endpoint spec
4. Implementation in `check.py`:
   - Add `microware_submit(domain)` function that calls the backorder endpoint
   - Two strategies — pick one:
     - **A) Telegram inline approve buttons** (recommended start): each notification has ✅ Backorder / ❌ Skip buttons. Click → script submits via API. Requires Telegram bot webhook handling, so the GitHub Actions cron model needs supplementing with a small persistent listener (or a serverless function). Cost-conscious.
     - **B) Tier-based auto-submit**: only the highest-quality reasons (e.g. dictionary match in EN/HU, NOT short-only, NOT compound) auto-submit. Pure cron model still works. Risk: each submitted backorder costs 2 604 Ft on success — at 5–10 catches/month that's manageable, but a config bug could rack up costs fast.
   - Add `dry_run` mode for testing API integration without spending money
5. Cost cap: implement a daily/monthly submission limit in config (e.g. `max_backorders_per_day: 3`) regardless of strategy

### Open questions

- Confirm pre-deletion parking is currently 31 or 60 days (affects how we display "free on" date in notifications)
- Whether the microware API supports cancellation of an already-submitted backorder
- Whether multiple registrars submitting backorder for the same domain compete fairly (i.e. is it worth submitting to BOTH microware AND keeping a manual domdom watchlist?)

## Tuning history

- Started: `max_short_length: 5`, `min_word_zipf: 3.0`, no compound, no accent, no keywords.
- After first preview (858 matches): added first-run seed-skip, dropped `max_short_length` to 4, added `--test` mode, added digit-string and hyphen filters to dictionary check.
- After user feedback (5 example domains): added compound splitting, but initial `min_zipf 3.0` + `min_len 3` for parts gave 2039 noisy compound matches → tightened to `compound_min_part_zipf 4.0` + `compound_min_part_length 4` (600 matches, mostly real).
- Added accent-stripped HU + cross-lang compound + keyword list (current state).
- 2026-05-07: GHA scheduler refused to fire any cron run. Migrated from
  `schedule:` to external `repository_dispatch:` driven by cron-job.org;
  bumped cadence from "daily 08:00 UTC" to "every 1 minute". Repo also
  flipped public for unlimited Actions minutes.
- Today's totals: ~1400 cumulative matches across the full 30-day window. Daily delta expected ~15–40.

## Conventions

- User prefers concise responses. No trailing summaries when the diff speaks for itself.
- Push only when explicitly confirmed.
- Hungarian responses when user writes in Hungarian.
