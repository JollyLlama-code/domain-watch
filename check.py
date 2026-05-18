#!/usr/bin/env python3
"""
Scrape info.domain.hu's pre-deletion parking list, score domains, and notify
via ntfy when high-value ones appear.

State (which domains we've already notified about) lives in seen.json next
to this script.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from wordfreq import top_n_list, zipf_frequency

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
SEEN_PATH = ROOT / "seen.json"

# Shared with the Oracle brute-force launcher — subscribe to this topic in the
# ntfy phone app to receive both VM-ready pings and domain matches.
NTFY_TOPIC = "domwatch-m5dcuxgprlov6zea90i1"

# Prune seen entries older than this many days. The source page only shows
# domains parked in the last ~31 days, so 90 is a comfortable buffer.
SEEN_RETENTION_DAYS = 90

DOMAIN_ROW_RE = re.compile(r"^[a-z0-9\-]+\.hu$", re.IGNORECASE)


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


def _deaccent(s: str) -> str:
    """Strip combining accents. nyári → nyari, kávéház → kavehaz."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


@lru_cache(maxsize=1)
def _hu_deaccent_zipf_table() -> dict[str, float]:
    """{deaccented_form: max_zipf_among_originals}, built once on first use.
    Catches Hungarian domains that drop accents because URLs are ASCII
    (nyari → nyári, kavehaz → kávéház)."""
    table: dict[str, float] = {}
    for w in top_n_list("hu", 80000):
        if len(w) < 3 or any(c.isdigit() for c in w) or "-" in w or " " in w:
            continue
        d = _deaccent(w)
        if d == w:
            continue  # original had no accents — direct lookup already covers it
        z = zipf_frequency(w, "hu")
        if z > table.get(d, 0.0):
            table[d] = z
    return table


def _zipf(word: str, lang: str) -> float:
    """zipf_frequency with accent-stripping fallback for Hungarian."""
    z = zipf_frequency(word, lang)
    if z > 0 or lang != "hu":
        return z
    return _hu_deaccent_zipf_table().get(word, 0.0)


def is_dictionary_word(word: str, langs: list[str], min_zipf: float, min_len: int) -> str | None:
    """Return the language code if word is a known dictionary word, else None."""
    if len(word) < min_len:
        return None
    # Hyphenated labels (e-rms, you-are-more) get tokenized by wordfreq and
    # falsely match. A true dictionary domain is a single clean word.
    if "-" in word or "_" in word:
        return None
    # wordfreq returns nonzero frequencies for digit strings ("152", "650");
    # those should only count as all-numeric, not "dictionary".
    if word.isdigit():
        return None
    for lang in langs:
        if _zipf(word, lang) >= min_zipf:
            return lang
    return None


def is_compound_word(
    word: str, langs: list[str], min_zipf: float, min_len: int
) -> tuple[str, str, str, str] | None:
    """Try splitting `word` into two dictionary parts (cross-language allowed).
    Returns (left_lang, right_lang, left, right) or None.
    Each part must be >= min_len chars and >= min_zipf in some language."""
    if "-" in word or "_" in word or any(c.isdigit() for c in word):
        return None
    if len(word) < 2 * min_len:
        return None
    for split in range(min_len, len(word) - min_len + 1):
        left, right = word[:split], word[split:]
        left_lang = next(
            (l for l in langs if _zipf(left, l) >= min_zipf), None
        )
        if not left_lang:
            continue
        right_lang = next(
            (l for l in langs if _zipf(right, l) >= min_zipf), None
        )
        if right_lang:
            return (left_lang, right_lang, left, right)
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

    if triggers.get("compound"):
        compound = is_compound_word(
            name,
            cfg["wordlist_languages"],
            cfg.get("compound_min_part_zipf", 4.0),
            cfg.get("compound_min_part_length", 4),
        )
        if compound:
            left_lang, right_lang, left, right = compound
            tag = left_lang if left_lang == right_lang else f"{left_lang}+{right_lang}"
            reasons.append(f"{tag} compound ({left}+{right})")

    if triggers.get("keywords"):
        for kw in cfg.get("keywords", []):
            if kw and kw.lower() in name:
                reasons.append(f"keyword ({kw})")
                break

    if triggers.get("all_numeric") and name.isdigit():
        reasons.append("all-numeric")

    return reasons


def build_action_url(
    domain: str, *, tunnel_url: str, ttl_hours: int, now: int | None = None
) -> str:
    """Construct the HMAC-signed Backorder action URL.

    Empty tunnel_url or missing secret returns "" - caller falls back to a
    plain push without an action button.
    """
    if not tunnel_url:
        return ""
    secret = os.environ.get("BACKORDER_HMAC_SECRET", "")
    if not secret:
        return ""
    exp = (now if now is not None else int(time.time())) + ttl_hours * 3600
    sig = hmac.new(
        secret.encode("utf-8"),
        f"{domain}|{exp}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"{tunnel_url}?domain={domain}&exp={exp}&sig={sig}"


def build_ntfy_headers(*, title: str, action_url: str) -> dict[str, str]:
    """Headers for a single per-match ntfy push. Adds Backorder action when
    action_url is non-empty; otherwise sends a plain push."""
    headers = {"Title": title}
    if action_url:
        headers["Actions"] = f"http, Backorder, {action_url}, method=POST, clear=true"
    return headers


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


def run_test_notification(cfg: dict) -> int:
    """Fetch page, pick top dictionary matches, send a sample notification.
    Does not touch seen.json."""
    rows = fetch_domains(cfg["source_url"])
    if not rows:
        print("ERROR: parsed 0 domains.", file=sys.stderr)
        return 1

    matches: list[tuple[str, str, list[str]]] = []
    for domain, _parked, release in rows:
        reasons = score(domain, cfg)
        if reasons and any("word" in r for r in reasons):
            matches.append((domain, release, reasons))

    sample = sorted(matches, key=lambda r: (len(label(r[0])), r[0]))[:8]
    if not sample:
        print("No dictionary matches today to use for a test.")
        return 1

    title = f"[TEST] Domain watch - {len(sample)} match"
    body = "\n".join(d for d, _, _ in sample)
    print(title + "\n" + body)
    ntfy_send({"Title": title}, body)
    print("Test notification sent.")
    return 0


def main() -> int:
    cfg = load_config()

    if "--test" in sys.argv:
        return run_test_notification(cfg)

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
        tunnel_url = cfg.get("backorder", {}).get("tunnel_url", "")
        ttl_hours = cfg.get("backorder", {}).get("action_ttl_hours", 24)
        for domain, _release, reasons in matches:
            reason_summary = ", ".join(reasons[:2])
            title = f"{domain} - {reason_summary}"
            print(title)
            action_url = build_action_url(
                domain, tunnel_url=tunnel_url, ttl_hours=ttl_hours
            )
            headers = build_ntfy_headers(title=title, action_url=action_url)
            ntfy_send(headers, "")

    save_seen(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
