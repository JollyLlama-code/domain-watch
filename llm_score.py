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
    "(pl. 'xkqztr'). A 'category' mező értékteleneknél legyen üres string. "
    "A 'domain' mezőben pontosan a kapott stringet add vissza (pl. 'szeletelo.hu')."
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


def classify_domains(names: list[str], cfg: dict, client=None):
    """Return verdict dict, or None to signal fallback. {} for empty input.

    Callers are responsible for checking cfg['llm']['enabled'] before invoking;
    this function does not consult the enabled flag.
    """
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
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": "Értékeld a következő domaineket:\n" + "\n".join(names),
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )
        text_block = next((b for b in resp.content if b.type == "text"), None)
        if text_block is None:
            print("llm_score: no text block in response", file=sys.stderr)
            return None
        data = json.loads(text_block.text)
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
