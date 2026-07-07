"""
bot.py — required submission entry point (challenge-brief.md §7.1).

    def compose(category: dict, merchant: dict, trigger: dict, customer: dict | None) -> dict

Architecture (see README.md for the full writeup):

  1. engine/playbooks.py   — deterministic, fact-grounded first-message composer.
                             Dispatches on trigger["kind"] to one of ~20 playbook
                             families built directly against the 5-dimension
                             rubric (challenge-brief.md §8) and the anti-pattern
                             list (§11). Needs no LLM, no network call, and is
                             therefore trivially deterministic and <30s.
  2. engine/llm.py         — OPTIONAL polish pass. If ANTHROPIC_API_KEY is set,
                             asks Claude to rewrite the draft's prose for a more
                             natural voice, under a hard constraint not to add
                             any fact absent from the draft/context. Falls back
                             silently to the deterministic draft on any error,
                             timeout, or missing key.
  3. engine/validate.py    — cheap regex-level self-check against the anti-
                             pattern list (multi-CTA, generic discount when a
                             service+price offer exists, taboo vocabulary,
                             verbatim repeats, missing Hindi-English mix when
                             expected, banned preambles). Repairs are
                             conservative (strip only, never invent).

This module is intentionally free of any FastAPI/server concerns — server.py
imports it and wraps it for the 5 HTTP endpoints in challenge-testing-brief.md.
"""
from __future__ import annotations
from typing import Optional

from engine.playbooks import compose_playbook
from engine.validate import validate_and_repair
from engine.llm import polish as llm_polish
from engine import facts as f


def compose(category: dict, merchant: dict, trigger: dict, customer: Optional[dict] = None) -> dict:
    result = compose_playbook(category, merchant, trigger, customer)

    body, cta = result.body, result.cta
    rationale = result.rationale

    polished = llm_polish(body, cta, category, merchant, trigger, customer)
    if polished:
        body = polished.get("body", body)
        cta = polished.get("cta", cta)
        rationale = polished.get("rationale", rationale) + " [llm-polished]"

    wants_hindi = f.wants_hindi_mix(merchant=merchant, customer=customer)
    body, warnings = validate_and_repair(body, cta, category, merchant, wants_hindi=wants_hindi)
    # "expected_hindi_english_mix_not_detected" is a soft signal (many good
    # messages for a bilingual merchant are still fine in English, e.g.
    # clinical-voice categories) — don't clutter the judge-facing rationale
    # with it, but keep everything else since those are real anti-pattern hits.
    hard_warnings = [w for w in warnings if w != "expected_hindi_english_mix_not_detected"]
    if hard_warnings:
        rationale += f" [validator flags: {', '.join(hard_warnings)}]"

    suppression_key = trigger.get("suppression_key") or f"{trigger.get('kind')}:{merchant.get('merchant_id')}"

    return {
        "body": body,
        "cta": cta,
        "send_as": result.send_as,
        "suppression_key": suppression_key,
        "rationale": rationale,
    }


if __name__ == "__main__":
    # tiny smoke test using the base dataset
    import json
    from pathlib import Path

    DATA = Path(__file__).parent / "dataset"
    category = json.loads((DATA / "categories" / "dentists.json").read_text())
    merchant = json.loads((DATA / "merchants" / "m_001_drmeera_dentist_delhi.json").read_text())
    trigger = json.loads((DATA / "triggers" / "trg_001_research_digest_dentists.json").read_text())
    out = compose(category, merchant, trigger, None)
    print(json.dumps(out, indent=2, ensure_ascii=False))
