# Email channel for the backorder match list — design

Date: 2026-06-03

## Goal

Mirror the per-match ntfy notifications (the "1-button backorder list") to an
email address, with a clickable per-row backorder action that is safe against
email-client link prefetching.

## Context

- `check.py` runs in GitHub Actions (repository_dispatch every minute). For each
  new match it sends an ntfy push whose `Actions` header carries an HTTP `POST`
  button to `tunnel_url` (`https://backorder.babakocsiszakaruhaz.hu/backorder`),
  signed with an HMAC over `domain|exp` (`build_action_url`).
- `backorder_api.py` (FastAPI, on the user's server behind a cloudflared tunnel)
  exposes `POST /backorder?domain&exp&sig`: verifies HMAC, enforces `daily_cap`
  + same-day dedup, then calls microware. Reads params from the query string.
- `auto_backorder_domains` (e.g. `babakocsi.hu`) are skipped from notifications;
  they are auto-submitted.

## Key constraints

- A real backorder costs ~2 604 Ft on success. Email clients and link scanners
  (Gmail, Outlook SafeLinks) **prefetch links via GET**. Therefore the email
  link must NOT itself book — it must open a confirmation page, and an explicit
  button there performs the booking. The actual booking stays a `POST`.
- Email must give full control over the link/markup → send via Gmail SMTP with
  an app password (decided with user), not the ntfy `Email` header.

## Design

### 1. Confirmation endpoint — `backorder_api.py`

Add `GET /confirm?domain&exp&sig`. Returns a minimal HTML page showing the
domain and a single "Megerősítem a foglalást" button inside a form:

```
<form method="POST" action="{tunnel_url}?domain={domain}&exp={exp}&sig={sig}">
  <button type="submit">Megerősítem a foglalást</button>
</form>
```

- The GET does NOT book — a prefetch of this URL is harmless.
- The booking is still `POST /backorder`, which validates the HMAC and enforces
  cap/dedup exactly as today. No change to booking logic.
- The GET may render regardless of signature validity; safety comes from the
  POST. (Optional nicety: show "lejárt link" text if `exp` is past, but the
  POST already rejects it.)

### 2. Email sender — new `email_notify.py`

Single responsibility, mirrors `notify.py`'s thin design (no wordfreq import):

```python
def email_send(*, to: str, sender: str, subject: str, html: str) -> None
```

- SMTP over `smtp.gmail.com:587`, STARTTLS, login `sender` + `GMAIL_APP_PASSWORD`
  (from env). Builds a `multipart/alternative` (plaintext + HTML) message.
- Wrapped in try/except: a send failure prints to stderr and returns — it must
  never abort `check.py` (same fault-isolation as `ntfy_send`).

### 3. Digest email — `check.py`

In `main()`, in the same `if matches:` block that sends ntfy pushes, after the
per-match loop, build ONE digest email per run (only if email enabled and there
is at least one non-auto match):

- Subject: e.g. `Domain watch — {N} találat`.
- Body: an HTML list, one row per match: `domain — reason_summary` followed by a
  "Lefoglalás →" link to `confirm_url?domain&exp&sig`, where the signed URL is
  built with the existing HMAC scheme (reuse `build_action_url` logic, pointing
  at `confirm_url` instead of `tunnel_url`).
- `auto_backorder_domains` are excluded from the list (same as ntfy).
- Per-row links only — no "book all" button.
- If `BACKORDER_HMAC_SECRET` / `confirm_url` is missing, fall back to a plain
  list with no links (mirrors `build_action_url` returning "").

### 4. Config + secrets

`config.json`:

```json
"notify": {
  "email": {
    "enabled": true,
    "to": "parapet@freestart.hu",
    "from": "jollylama06@gmail.com"
  }
},
"backorder": {
  ...
  "confirm_url": "https://backorder.babakocsiszakaruhaz.hu/confirm"
}
```

Secrets / workflow (`.github/workflows/check.yml`):

- New GitHub secret `GMAIL_APP_PASSWORD`.
- Add to the `python check.py` step env: `GMAIL_APP_PASSWORD` **and**
  `BACKORDER_HMAC_SECRET` (the latter is required to sign both the ntfy action
  and the email confirm link; it is currently not passed, so links go out
  unsigned/empty — this fix benefits ntfy too).

### 5. Tests (TDD)

- `email_notify`: SMTP client mocked — asserts host/port/STARTTLS/login,
  multipart structure, recipient/subject/HTML; a send exception is swallowed
  (no raise).
- `/confirm`: GET returns 200 and the HTML contains a POST form whose action is
  the correct `/backorder?domain&exp&sig` URL; confirms the GET path performs no
  booking (no microware call).
- `check.py`: with matches and email enabled → exactly one email containing all
  non-auto matches; `auto_backorder_domains` excluded; email disabled → no send;
  missing secret → list rendered without links.

## Out of scope

- No change to scoring, seen.json, ntfy behaviour (beyond wiring the secret),
  IP-guard, or test-notification paths.
- No "book all" / batch action.
- No new email provider/account beyond the existing Gmail.

## Cost safety summary

Actual booking remains `POST`-only with HMAC + `daily_cap` + same-day dedup in
`backorder_api`. The email/`/confirm` GET cannot book. Prefetch-safe by design.
