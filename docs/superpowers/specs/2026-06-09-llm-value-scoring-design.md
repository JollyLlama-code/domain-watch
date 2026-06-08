# LLM-based business-value scoring for domain-watch

**Date:** 2026-06-09
**Status:** Approved (design)

## Problem

The current `score()` in `check.py` decides if a `.hu` domain is worth notifying
about using rule-based filters: short labels, single dictionary words (wordfreq),
compound splits, a keyword list, and all-numeric. This misses domains that have
real business value but aren't in wordfreq — most visibly Hungarian
inflected/derived forms. The trigger example is `szeletelo.hu` ("szeletelő" =
slicer, a product-category word): not short, not in wordfreq even accented, so it
never matched and never notified.

"Business value" is not a grammatical or dictionary property — a wordlist can only
say whether something *is a word*, not whether it is *valuable*. So the user chose
to have an LLM make the value judgment.

## Decisions (from brainstorming)

- **Mechanism:** an LLM judges business value (not a bigger dictionary or a
  suffix-stripping heuristic).
- **Scope:** the LLM judges **all** new domains and becomes the primary value
  filter. The existing rule-based `score()` is kept only as a fallback.
- **Model:** Claude Haiku 4.5 (`claude-haiku-4-5`) — cheap, fast, sufficient for a
  word-value judgment. Pricing: $1.00/1M input, $5.00/1M output.
- **On API failure:** fall back to rule-based `score()` for that run **and** send an
  ntfy alert. Domains are marked seen normally. Accepted trade-off: a
  rules-missed valuable domain on a failed run is lost (failures are rare and
  alerted).
- **Cost:** realistic ~200–750 HUF/month at the current ~15–40 new domains/day,
  judged in a single batched call per run. A per-run cap guards against runaway
  cost if `seen.json` is ever wiped.

## Architecture

New module **`llm_score.py`** — single responsibility, kept separate so the
backorder API and other callers don't pull in the anthropic SDK.

```python
def classify_domains(names: list[str], cfg: dict) -> dict[str, dict] | None:
    """Judge business value of domain labels via Claude Haiku.

    Returns {domain: {"valuable": bool, "category": str}} on success,
    or None on any failure (missing key, API error, cap exceeded) — None
    signals the caller to fall back to rule-based scoring.
    """
```

- Reads `ANTHROPIC_API_KEY` from the environment (populated by `load_secrets`,
  same as the other secrets).
- One batched `client.messages` call covering every domain passed in.
- Uses the official `anthropic` SDK (added to `requirements.txt`).
- Structured output via `output_config.format` (JSON schema) so the response is a
  reliable per-domain array.
- If `len(names) > cfg["llm"]["max_domains_per_run"]`, return `None` immediately
  (cost fuse) — do not call the API.
- If `ANTHROPIC_API_KEY` is missing, return `None`.
- Catch all anthropic/exception paths, print to stderr, return `None`.

`score()` in `check.py` is unchanged.

## `main()` flow (check.py)

```
1. fetch rows from source
2. new domains = those not in seen  → seen[domain] = today
3. if first run → silent seed, return (unchanged; no LLM call)
4. value decision for the new domains:
     verdicts = llm_score.classify_domains(new_names, cfg)   (only if cfg.llm.enabled)
     if verdicts is not None:
         matches = [(domain, release, ["AI: " + category])
                    for domain in new if verdicts[domain]["valuable"]]
     else:                       # disabled, failed, or cap exceeded
         if cfg.llm.enabled:     # i.e. it was meant to run but failed
             ntfy alert: "LLM scoring failed — fell back to rule scoring"
         matches via existing rule-based score() path
5. notify: existing ntfy per-match + email digest (unchanged)
6. save_seen
```

Notes:
- A domain that the LLM returns but does not mark valuable simply isn't a match.
- A domain missing from the LLM response is treated as not valuable (defensive).
- The reason string `"AI: <category>"` flows unchanged into the existing ntfy
  title and the email digest (`reasons[:2]`), and into the
  `auto_backorder_domains` exclusion filter.

## Config — new `llm` block (config.json)

```json
"llm": {
  "enabled": true,
  "model": "claude-haiku-4-5",
  "max_domains_per_run": 200
}
```

- `enabled: false` → skip the LLM entirely; behave exactly as today (rule-based).
  This is the escape hatch if the API is ever a problem.
- `model` → kept in config so it can be changed without code edits.
- `max_domains_per_run` → cost fuse; over this, skip the LLM and fall back.

## Prompt & structured output

System prompt (Hungarian), in essence: *"You evaluate soon-to-be-released `.hu`
domains for a domain investor. For each label decide whether it is worth
backordering (has resale / business value) and give a short Hungarian category."*
Include a few worked examples (e.g. `szeletelo` → valuable / "konyhai eszköz";
random consonant strings → not valuable).

Output constrained by a JSON schema — array of objects:

```json
[{"domain": "szeletelo.hu", "valuable": true, "category": "konyhai eszköz"}]
```

`additionalProperties: false` on each object; `valuable` boolean, `category`
string, `domain` string.

## Secrets / deployment

- Add `ANTHROPIC_API_KEY` to the server's `secrets.env` (the scheduled task
  `DomainWatch` is the real executor) — never pasted into chat.
- If GitHub Actions is ever re-enabled, add it as an Actions secret too.

## Failure handling (chosen)

- LLM exception / `None` / cap exceeded → that run decides notifications via
  rule-based `score()`, and an ntfy alert fires (only when `llm.enabled` is true,
  so a deliberately-disabled config stays quiet).
- Domains are marked seen normally on a failed run. Trade-off accepted: a
  rules-missed valuable domain on a failed run is not re-judged later.

## Out of scope

- No retry/queue of failed domains for re-judging next run.
- No change to the rule-based `score()` itself.
- No change to the backorder, email, or ntfy delivery mechanisms.

## Testing

- `classify_domains` returns `None` when the key is missing, when the cap is
  exceeded, and on a simulated API error (mock the anthropic client).
- `classify_domains` parses a well-formed structured response into the verdict
  dict.
- `main()` uses LLM verdicts when present; falls back to `score()` and alerts when
  `classify_domains` returns `None`; skips the LLM on first run.
- Existing rule-based and notification tests stay green.
