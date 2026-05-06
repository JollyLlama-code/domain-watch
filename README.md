# domain-watch

Daily checks the [info.domain.hu pre-deletion parking list](https://info.domain.hu/parkolas/hu/ido.html) and pings a Telegram bot when a high-value `.hu` domain is about to free up.

A domain qualifies as **high value** when it matches any rule in `config.json`:

- **Short**: label length ≤ `max_short_length` (default 5).
- **Dictionary**: the label is a known word in any `wordlist_languages` (default `en`, `hu`) above `min_word_zipf_frequency` (default 3.0 — common-ish).
- **All-numeric**: the label is digits only (e.g. `7777.hu`).

Matched domains are deduplicated via `seen.json`, which the workflow commits back so you don't get re-notified.

---

## One-time setup

### 1. Create the Telegram bot

1. On Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot`, follow the prompts. Save the **bot token**.
2. Message your new bot once (any text — required to "open" the chat).
3. In a browser, open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`. Find `"chat":{"id":...}` in the JSON. That number is your **chat id**.

### 2. Push to GitHub

```powershell
cd C:\Users\User\Documents\domain-watch
git init
git add .
git commit -m "Initial commit"
gh repo create domain-watch --private --source=. --push
```

(Or create the repo on github.com manually and push.)

### 3. Add secrets

In the repo: **Settings → Secrets and variables → Actions → New repository secret**

- `TELEGRAM_BOT_TOKEN` — the bot token from BotFather
- `TELEGRAM_CHAT_ID` — the chat id from step 1

### 4. Verify

Trigger the workflow manually: **Actions → Domain watch → Run workflow**.
The first run will mark every current domain as "seen" and send a notification listing today's high-value matches.

---

## Tuning

Edit `config.json`:

| Field | Effect |
|---|---|
| `max_short_length` | Drop to 4 for stricter, raise to 6 for looser. |
| `min_word_zipf_frequency` | Lower (e.g. 2.5) for more obscure words; raise (e.g. 4.0) for common-only. |
| `wordlist_languages` | Add `de`, `fr`, etc. — any [wordfreq-supported](https://github.com/rspeer/wordfreq#sources-and-supported-languages) language. |
| `notify_on.*` | Toggle individual rules. |
| `ignore_substrings` | Substrings that disqualify a label (default `xn--` for IDN domains). |

## Local test

```powershell
cd C:\Users\User\Documents\domain-watch
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Dry run (no Telegram credentials → just prints what would be sent):
python check.py

# Real run:
$env:TELEGRAM_BOT_TOKEN = "..."
$env:TELEGRAM_CHAT_ID = "..."
python check.py
```

The first local run will populate `seen.json` with every domain currently on the page. Delete `seen.json` to start fresh.
