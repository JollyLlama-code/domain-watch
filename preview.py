"""One-shot preview: scores every domain on today's page and prints matches.
Does not touch seen.json and does not send Telegram. Saves full list to today_matches.txt.
"""
from collections import Counter
from pathlib import Path

from check import fetch_domains, label, load_config, score


def main() -> int:
    cfg = load_config()
    rows = fetch_domains(cfg["source_url"])
    print(f"Fetched {len(rows)} domains.\n")

    matches: list[tuple[str, str, list[str]]] = []
    for domain, _parked, release in rows:
        reasons = score(domain, cfg)
        if reasons:
            matches.append((domain, release, reasons))

    # Bucket by primary reason category (ordered by signal quality)
    buckets: dict[str, list[tuple[str, str, list[str]]]] = {
        "dictionary (en or hu)": [],
        "compound (en or hu)": [],
        "all-numeric": [],
        "short only": [],
    }
    for m in matches:
        domain, _, reasons = m
        joined = ", ".join(reasons)
        if "word" in joined:
            buckets["dictionary (en or hu)"].append(m)
        elif "compound" in joined:
            buckets["compound (en or hu)"].append(m)
        elif "all-numeric" in joined:
            buckets["all-numeric"].append(m)
        else:
            buckets["short only"].append(m)

    print(f"=== Total matches: {len(matches)} ===\n")
    for name, items in buckets.items():
        print(f"--- {name}: {len(items)} ---")
        # Sort by label length asc, then alpha
        items.sort(key=lambda r: (len(label(r[0])), r[0]))
        for domain, release, reasons in items[:30]:
            print(f"  {domain:30s}  {', '.join(reasons)}")
        if len(items) > 30:
            print(f"  ...and {len(items) - 30} more")
        print()

    # Length histogram for "short" category
    lens = Counter(len(label(d)) for d, _, r in matches if any("short" in x for x in r))
    print("Label-length histogram (short matches):")
    for length in sorted(lens):
        print(f"  {length} chars: {lens[length]}")

    # Full dump
    out = Path(__file__).parent / "today_matches.txt"
    with out.open("w", encoding="utf-8") as f:
        for domain, release, reasons in sorted(matches, key=lambda r: (len(label(r[0])), r[0])):
            f.write(f"{domain}\t{release}\t{', '.join(reasons)}\n")
    print(f"\nFull list ({len(matches)} rows) saved to: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
