#!/usr/bin/env python3
"""
Scrape info.domain.hu's pre-deletion parking list, score domains, and notify
via Telegram when high-value ones appear.

State (which domains we've already notified about) lives in seen.json and is
committed back to the repo by the GitHub Actions workflow.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from wordfreq import zipf_frequency

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
SEEN_PATH = ROOT / "seen.json"

# Prune seen entries older than this many days. The source page only shows
# domains parked in the last ~31 days, so 90 is a comfortable buffer.
SEEN_RETENTION_DAYS = 90

DOMAIN_ROW_RE = re.compile(r"^[a-z0-9\-áéíóöőúüű]+\.hu$", re.IGNORECASE)


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_seen() -> dict[str, str]:
    if not SEEN_PATH.exists():
        return {}
    try:
        return json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_seen(seen: dict[str, str]) -> None:
    cutoff = (date.today() - timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    pruned = {d: t for d, t in seen.items() if t >= cutoff}
    SEEN_PATH.write_text(
        json.dumps(pruned, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def fetch_domains(url: str) -> list[tuple[str, str, str]]:
    """Returns list of (domain, parked_date, release_date) tuples."""
    resp = requests.get(url, timeout=30, headers={"User-Agent": "domain-watch/1.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    rows: list[tuple[str, str, str]] = []
    for tr in soup.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 4:
            continue
        # Columns: serial, domain, parked_date, release_date
        domain = cells[1].lower()
        if not DOMAIN_ROW_RE.match(domain):
            continue
        rows.append((domain, cells[2], cells[3]))
    return rows


def label(domain: str) -> str:
    """Return the part before .hu."""
    return domain.rsplit(".hu", 1)[0]


def is_dictionary_word(word: str, langs: list[str], min_zipf: float, min_len: int) -> str | None:
    """Return the language code if word is a known dictionary word, else None."""
    if len(word) < min_len:
        return None
    # Hyphenated labels (e-rms, you-are-more) get tokenized by wordfreq and
    # falsely match. A true dictionary domain is a single clean word.
    if "-" in word or "_" in word:
        return None
    for lang in langs:
        if zipf_frequency(word, lang) >= min_zipf:
            return lang
    return None


def score(domain: str, cfg: dict) -> list[str]:
    """Return a list of reason tags if the domain qualifies as high-value, else []."""
    name = label(domain)
    reasons: list[str] = []

    for sub in cfg.get("ignore_substrings", []):
        if sub in name:
            return []

    triggers = cfg["notify_on"]

    if triggers.get("short") and len(name) <= cfg["max_short_length"]:
        reasons.append(f"short ({len(name)} chars)")

    if triggers.get("dictionary"):
        lang = is_dictionary_word(
            name,
            cfg["wordlist_languages"],
            cfg["min_word_zipf_frequency"],
            cfg["min_word_length"],
        )
        if lang:
            reasons.append(f"{lang} word")

    if triggers.get("all_numeric") and name.isdigit():
        reasons.append("all-numeric")

    return reasons


def telegram_send(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("WARNING: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set; skipping send.")
        print("Would have sent:\n" + text)
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
            "parse_mode": "HTML",
        },
        timeout=30,
    )
    resp.raise_for_status()


def format_message(matches: list[tuple[str, str, list[str]]]) -> str:
    lines = [f"<b>Domain watch</b> — {len(matches)} high-value match(es)"]
    for domain, release_date, reasons in matches:
        tags = ", ".join(reasons)
        lines.append(f"• <code>{domain}</code> — {tags} (free {release_date})")
    return "\n".join(lines)


def main() -> int:
    cfg = load_config()
    seen = load_seen()
    today_iso = date.today().isoformat()
    is_first_run = not seen

    rows = fetch_domains(cfg["source_url"])
    if not rows:
        print("ERROR: parsed 0 domains — page format may have changed.", file=sys.stderr)
        return 1

    print(f"Fetched {len(rows)} domains from source.")

    matches: list[tuple[str, str, list[str]]] = []
    new_count = 0
    for domain, _parked, release in rows:
        if domain in seen:
            continue
        new_count += 1
        seen[domain] = today_iso
        reasons = score(domain, cfg)
        if reasons:
            matches.append((domain, release, reasons))

    if is_first_run:
        print(f"First run: seeded {new_count} domains into seen.json. Skipping notification.")
        save_seen(seen)
        return 0

    print(f"{new_count} new since last run, {len(matches)} matched filters.")

    if matches:
        message = format_message(matches)
        print(message)
        telegram_send(message)

    save_seen(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
