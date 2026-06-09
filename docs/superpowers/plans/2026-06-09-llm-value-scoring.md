# LLM Business-Value Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Notify on `.hu` domains that have business value (e.g. `szeletelo.hu`) by having Claude Haiku judge each newly-parked domain, with the existing rule-based scorer kept as a fallback.

**Architecture:** A new `llm_score.py` module exposes `classify_domains(names, cfg, client=None) -> dict | None`, calling Claude Haiku 4.5 in one batched, schema-constrained request. `check.py` gains a testable `decide_matches()` seam that uses LLM verdicts when available and falls back to the existing `score()` (plus an ntfy alert) on any LLM failure. `main()` is rewired to use it.

**Tech Stack:** Python, `anthropic` SDK (Claude Haiku 4.5), pytest + monkeypatch, existing `notify.ntfy_send` and `load_secrets`.

---

## File Structure

- Create: `llm_score.py` — LLM value judgment, self-contained, returns `None` on any failure.
- Create: `tests/test_llm_score.py` — unit tests for `classify_domains` with a stub client.
- Create: `tests/test_decide_matches.py` — unit tests for the `decide_matches` seam.
- Modify: `requirements.txt` — add `anthropic`.
- Modify: `config.json` — add `llm` block.
- Modify: `check.py` — add `import llm_score`, add `decide_matches()`, rewire `main()`.

---

## Task 1: Dependencies and config

**Files:**
- Modify: `requirements.txt`
- Modify: `config.json:1-43`

- [ ] **Step 1: Add the SDK to requirements**

Append this line to `requirements.txt` (latest SDK — `output_config.format` needs a current version):

```
anthropic
```

- [ ] **Step 2: Install it**

Run: `pip install anthropic`
Expected: installs cleanly; `python -c "import anthropic; print(anthropic.__version__)"` prints a version.

- [ ] **Step 3: Add the `llm` block to config.json**

Insert this block after the `"compound_min_part_length": 4,` line (i.e. among the top-level tunables, before `"notify_on"`):

```json
  "llm": {
    "enabled": true,
    "model": "claude-haiku-4-5",
    "max_domains_per_run": 200
  },
```

- [ ] **Step 4: Verify config still parses**

Run: `python -c "import json; print(json.load(open('config.json', encoding='utf-8-sig'))['llm'])"`
Expected: `{'enabled': True, 'model': 'claude-haiku-4-5', 'max_domains_per_run': 200}`

- [ ] **Step 5: Commit**

```bash
git add requirements.txt config.json
git commit -m "chore: add anthropic dep + llm config block"
```

---

## Task 2: `llm_score.classify_domains` — guards and module skeleton

`classify_domains` returns `None` on anything that should trigger fallback (cap exceeded, missing API key, API error) and `{}` for an empty input. This task creates the full module; its tests cover the guard paths that return before any network call.

**Files:**
- Create: `llm_score.py`
- Test: `tests/test_llm_score.py`

- [ ] **Step 1: Write the failing guard tests**

Create `tests/test_llm_score.py`:

```python
"""Tests for llm_score.classify_domains."""
from __future__ import annotations

import json

import llm_score


def _cfg(cap: int = 200, enabled: bool = True) -> dict:
    return {"llm": {"enabled": enabled, "model": "claude-haiku-4-5",
                    "max_domains_per_run": cap}}


def test_empty_input_returns_empty_dict(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert llm_score.classify_domains([], _cfg()) == {}


def test_cap_exceeded_returns_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    names = [f"d{i}.hu" for i in range(5)]
    assert llm_score.classify_domains(names, _cfg(cap=3)) is None


def test_missing_api_key_returns_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm_score.classify_domains(["szeletelo.hu"], _cfg()) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_llm_score.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm_score'`

- [ ] **Step 3: Create the module**

Create `llm_score.py`:

```python
"""Judge business value of .hu domain labels via Claude Haiku.

classify_domains returns {domain: {"valuable": bool, "category": str}} on
success, or None on any failure (cap exceeded, missing key, API error) so the
caller can fall back to rule-based scoring. Kept separate from check.py so other
callers don't pull in the anthropic SDK.
"""
from __future__ import annotations

import json
import os
import sys

MODEL_DEFAULT = "claude-haiku-4-5"

SYSTEM_PROMPT = (
    "Egy .hu domainbefektetőnek értékeled a hamarosan felszabaduló domaineket. "
    "Minden domainre döntsd el, érdemes-e lefoglalni (van-e újraértékesítési "
    "vagy üzleti értéke), és adj egy rövid magyar kategóriát. Értékesnek számít "
    "egy értelmes magyar vagy angol szó/kifejezés, termékkategória, szolgáltatás "
    "vagy jól márkázható név (pl. 'szeletelo' -> konyhai eszköz, 'borklub' -> "
    "bor/közösség). Nem értékes a véletlenszerű betűhalmaz vagy értelmetlen string "
    "(pl. 'xkqztr'). A 'category' mező értékteleneknél legyen üres string."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string"},
                    "valuable": {"type": "boolean"},
                    "category": {"type": "string"},
                },
                "required": ["domain", "valuable", "category"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def classify_domains(names, cfg, client=None):
    """Return verdict dict, or None to signal fallback. {} for empty input."""
    if not names:
        return {}

    llm_cfg = cfg.get("llm", {})
    cap = llm_cfg.get("max_domains_per_run", 200)
    if len(names) > cap:
        print(
            f"llm_score: {len(names)} domains exceeds cap {cap}; skipping LLM",
            file=sys.stderr,
        )
        return None

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("llm_score: ANTHROPIC_API_KEY not set; skipping LLM", file=sys.stderr)
        return None

    try:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        resp = client.messages.create(
            model=llm_cfg.get("model", MODEL_DEFAULT),
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": "Értékeld a következő domaineket:\n" + "\n".join(names),
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )
        text = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(text)
        out = {}
        for item in data.get("results", []):
            domain = item.get("domain")
            if domain:
                out[domain] = {
                    "valuable": bool(item.get("valuable")),
                    "category": item.get("category", ""),
                }
        return out
    except Exception as e:
        print(f"llm_score FAILED: {e}", file=sys.stderr)
        return None
```

- [ ] **Step 4: Run the guard tests to verify they pass**

Run: `python -m pytest tests/test_llm_score.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add llm_score.py tests/test_llm_score.py
git commit -m "feat: llm_score.classify_domains guards (cap, missing key, empty)"
```

---

## Task 3: `classify_domains` — parse a successful response

**Files:**
- Test: `tests/test_llm_score.py` (add)

- [ ] **Step 1: Write the failing parse test**

Append to `tests/test_llm_score.py`:

```python
class _Block:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _Resp:
    def __init__(self, text: str):
        self.content = [_Block(text)]


class _StubMessages:
    def __init__(self, text: str):
        self._text = text
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _Resp(self._text)


class _StubClient:
    def __init__(self, text: str):
        self.messages = _StubMessages(text)


def test_parses_structured_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    payload = json.dumps(
        {
            "results": [
                {"domain": "szeletelo.hu", "valuable": True, "category": "konyhai eszköz"},
                {"domain": "xkqztr.hu", "valuable": False, "category": ""},
            ]
        }
    )
    client = _StubClient(payload)
    out = llm_score.classify_domains(["szeletelo.hu", "xkqztr.hu"], _cfg(), client=client)
    assert out == {
        "szeletelo.hu": {"valuable": True, "category": "konyhai eszköz"},
        "xkqztr.hu": {"valuable": False, "category": ""},
    }
    assert client.messages.last_kwargs["model"] == "claude-haiku-4-5"
```

- [ ] **Step 2: Run it to verify it passes**

Run: `python -m pytest tests/test_llm_score.py::test_parses_structured_response -v`
Expected: PASS (the implementation from Task 2 already handles this).

- [ ] **Step 3: Commit**

```bash
git add tests/test_llm_score.py
git commit -m "test: llm_score parses structured verdict response"
```

---

## Task 4: `classify_domains` — API error returns None

**Files:**
- Test: `tests/test_llm_score.py` (add)

- [ ] **Step 1: Write the failing error test**

Append to `tests/test_llm_score.py`:

```python
class _RaisingMessages:
    def create(self, **kwargs):
        raise RuntimeError("api down")


class _RaisingClient:
    messages = _RaisingMessages()


def test_api_error_returns_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    out = llm_score.classify_domains(["szeletelo.hu"], _cfg(), client=_RaisingClient())
    assert out is None
```

- [ ] **Step 2: Run it to verify it passes**

Run: `python -m pytest tests/test_llm_score.py::test_api_error_returns_none -v`
Expected: PASS (the `except Exception` path returns `None`).

- [ ] **Step 3: Run the whole module's tests**

Run: `python -m pytest tests/test_llm_score.py -v`
Expected: PASS (5 passed)

- [ ] **Step 4: Commit**

```bash
git add tests/test_llm_score.py
git commit -m "test: llm_score returns None on API error"
```

---

## Task 5: `decide_matches` seam in check.py

A pure, injectable function so the LLM-vs-fallback decision is unit-testable without the network. Returns `(matches, llm_failed)` where `matches` items are `(domain, release, reasons)` — the same shape the existing notify block consumes.

**Files:**
- Modify: `check.py` (add `import llm_score` near the other local imports at line 30-32; add `decide_matches` after `score()` which ends at line 213)
- Test: `tests/test_decide_matches.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_decide_matches.py`:

```python
"""Tests for check.decide_matches (LLM-vs-fallback decision)."""
from __future__ import annotations

import check


def _cfg(enabled: bool = True) -> dict:
    cfg = check.load_config()
    cfg.setdefault("llm", {})
    cfg["llm"]["enabled"] = enabled
    return cfg


def test_uses_llm_verdicts_when_present():
    rows = [("szeletelo.hu", "p", "2026-07-01"), ("xkqztr.hu", "p", "2026-07-02")]

    def fake_classify(names, cfg, client=None):
        return {
            "szeletelo.hu": {"valuable": True, "category": "konyhai eszköz"},
            "xkqztr.hu": {"valuable": False, "category": ""},
        }

    matches, llm_failed = check.decide_matches(rows, _cfg(), classify_fn=fake_classify)
    assert llm_failed is False
    assert matches == [("szeletelo.hu", "2026-07-01", ["AI: konyhai eszköz"])]


def test_missing_verdict_treated_as_not_valuable():
    rows = [("szeletelo.hu", "p", "2026-07-01")]

    def fake_classify(names, cfg, client=None):
        return {}  # domain absent from response

    matches, llm_failed = check.decide_matches(rows, _cfg(), classify_fn=fake_classify)
    assert matches == []
    assert llm_failed is False


def test_fallback_to_rules_on_none_sets_flag():
    rows = [("auto.hu", "p", "2026-07-01")]  # "auto" is a dictionary word

    def fake_classify(names, cfg, client=None):
        return None  # simulate failure

    matches, llm_failed = check.decide_matches(rows, _cfg(), classify_fn=fake_classify)
    assert llm_failed is True
    assert any(d == "auto.hu" for d, _r, _reasons in matches)


def test_disabled_uses_rules_without_calling_llm():
    rows = [("auto.hu", "p", "2026-07-01")]

    def fake_classify(names, cfg, client=None):
        raise AssertionError("classify_fn must not be called when llm disabled")

    matches, llm_failed = check.decide_matches(
        rows, _cfg(enabled=False), classify_fn=fake_classify
    )
    assert llm_failed is False
    assert any(d == "auto.hu" for d, _r, _reasons in matches)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_decide_matches.py -v`
Expected: FAIL with `AttributeError: module 'check' has no attribute 'decide_matches'`

- [ ] **Step 3: Add the import**

In `check.py`, the local imports near line 30-32 currently read:

```python
from notify import NTFY_TOPIC, ntfy_send  # noqa: F401 — re-export for send_test_push.py and run_test_notification
from auto_backorder import run_auto_backorders
import email_notify
```

Add `llm_score`:

```python
from notify import NTFY_TOPIC, ntfy_send  # noqa: F401 — re-export for send_test_push.py and run_test_notification
from auto_backorder import run_auto_backorders
import email_notify
import llm_score
```

- [ ] **Step 4: Add `decide_matches` after `score()`**

`score()` ends with `return reasons` at line 213. Insert immediately after it:

```python
def decide_matches(new_rows, cfg, classify_fn=llm_score.classify_domains):
    """Decide which new domains to notify about.

    new_rows: list of (domain, parked, release) for domains not seen before.
    Returns (matches, llm_failed) where matches items are
    (domain, release, reasons). When the LLM is enabled but classify_fn returns
    None, fall back to rule-based score() and set llm_failed=True so the caller
    can alert.
    """
    if cfg.get("llm", {}).get("enabled"):
        verdicts = classify_fn([d for d, _p, _r in new_rows], cfg)
        if verdicts is not None:
            matches = []
            for domain, _parked, release in new_rows:
                verdict = verdicts.get(domain)
                if verdict and verdict.get("valuable"):
                    matches.append(
                        (domain, release, ["AI: " + verdict.get("category", "")])
                    )
            return matches, False
        llm_failed = True
    else:
        llm_failed = False

    matches = []
    for domain, _parked, release in new_rows:
        reasons = score(domain, cfg)
        if reasons:
            matches.append((domain, release, reasons))
    return matches, llm_failed
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_decide_matches.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add check.py tests/test_decide_matches.py
git commit -m "feat: decide_matches seam — LLM verdicts with rule fallback"
```

---

## Task 6: Rewire `main()` to use `decide_matches`

**Files:**
- Modify: `check.py:386-428` (the `main()` body from `seen = load_seen()` through the notify block)

- [ ] **Step 1: Replace the seen-loop + matches build + first-run block**

In `main()`, the current block (lines 386-406) is:

```python
    seen = load_seen()
    today_iso = date.today().isoformat()
    is_first_run = not seen

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
```

Replace it with:

```python
    seen = load_seen()
    today_iso = date.today().isoformat()
    is_first_run = not seen

    new_rows: list[tuple[str, str, str]] = []
    for domain, parked, release in rows:
        if domain in seen:
            continue
        seen[domain] = today_iso
        new_rows.append((domain, parked, release))

    if is_first_run:
        print(f"First run: seeded {len(new_rows)} domains into seen.json. Skipping notification.")
        save_seen(seen)
        return 0

    matches, llm_failed = decide_matches(new_rows, cfg)
    print(f"{len(new_rows)} new since last run, {len(matches)} matched filters.")

    if llm_failed:
        ntfy_send(
            {"Title": "domain-watch: LLM scoring failed"},
            "Fell back to rule-based scoring for this run.",
        )
```

The notify block that follows (current lines 408-426, `if matches:` … `notify_email(notifiable, cfg)`) and `save_seen(seen)` / `return 0` stay unchanged — `matches` has the same `(domain, release, reasons)` shape.

- [ ] **Step 2: Verify check.py imports cleanly**

Run: `python -c "import check"`
Expected: no output, exit 0.

- [ ] **Step 3: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS — all existing tests plus the new `test_llm_score.py` and `test_decide_matches.py`.

- [ ] **Step 4: Commit**

```bash
git add check.py
git commit -m "feat: main() uses LLM value scoring with rule fallback + alert"
```

---

## Task 7: Deployment note (manual, no code)

- [ ] **Step 1: Add the API key to the server**

Add `ANTHROPIC_API_KEY=sk-ant-...` to the server's `secrets.env` (the scheduled task `DomainWatch` is the real executor). Do **not** paste the key into chat or commit it. `secrets.env` is git-ignored and `load_secrets` reads it on import.

- [ ] **Step 2: Smoke-test on the server**

Run: `python check.py --test`
Expected: still prints sample dictionary matches (the `--test` path does not call the LLM; this just confirms the module imports and runs with the new dependency installed).

---

## Self-Review

- **Spec coverage:** new module `llm_score.py` (Tasks 2-4); `score()` unchanged, kept as fallback (Task 5); Haiku 4.5 model (Task 1 config + Task 2 default); LLM judges all new domains, primary filter (Task 5/6); failure → rules + ntfy alert (Task 5 flag, Task 6 alert); `llm` config block with `enabled`/`model`/`max_domains_per_run` (Task 1); cost fuse via cap (Task 2); first-run skip (Task 6 returns before `decide_matches`); structured output JSON schema + Hungarian prompt with examples (Task 2); `ANTHROPIC_API_KEY` in `secrets.env` (Task 7); tests for None on missing key/cap/error, parse success, main fallback (Tasks 2-5). All covered.
- **Placeholder scan:** no TBD/TODO; every code step shows full code.
- **Type consistency:** `classify_domains(names, cfg, client=None)` used identically in Tasks 2-5; verdict shape `{"valuable": bool, "category": str}` consistent; `decide_matches(new_rows, cfg, classify_fn=...)` returns `(matches, llm_failed)` consumed unchanged by the existing notify block; `matches` item shape `(domain, release, reasons)` matches the existing code.
