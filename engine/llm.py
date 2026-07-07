"""
llm.py — optional LLM-polish layer.

Design decision (see README): the deterministic playbook engine (engine/
playbooks.py) is the primary composer. It is fact-grounded by construction
(every claim traces to a context field) and needs no API key, no network
call, and is trivially deterministic and <30s as required by the brief.

If ANTHROPIC_API_KEY is set, this module asks Claude to *rewrite the prose*
of the playbook's draft for a more natural, less template-y voice — while
being given the draft plus the raw context and told explicitly not to
introduce any fact, number, name, or offer that isn't already in the draft
or the context. This bounds hallucination risk: the LLM is polishing, not
inventing from scratch. If the call fails or times out for any reason, the
deterministic draft is used as-is — the bot never blocks or degrades on LLM
unavailability.

Set LLM_MODE=off to force pure-deterministic mode regardless of API key
(useful for grading/testing without incurring API calls).
"""
from __future__ import annotations
import json
import os
import urllib.request
import urllib.error
from typing import Optional

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("VERA_LLM_MODEL", "claude-sonnet-4-6")
TIMEOUT_S = 20


def llm_available() -> bool:
    if os.environ.get("LLM_MODE", "").lower() == "off":
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


SYSTEM_PROMPT = """You are polishing a WhatsApp message drafted by a rule-based \
merchant-marketing assistant called Vera (magicpin). You will be given the \
draft message plus the structured context it was built from.

Rules (violating any of these is a failure):
1. Do NOT add any fact, number, date, price, name, offer, or citation that is \
not already present in the draft or the provided context JSON.
2. Keep exactly one primary call-to-action; do not add extra questions or choices.
3. Preserve the category voice (tone/register given in context) and the \
Hindi-English code-mix level of the draft (if the draft has Hindi-English \
mix, keep it; if it's pure English, keep it pure English).
4. Keep it concise — do not add a preamble ("I hope you're doing well" etc.) \
and do not re-introduce who "Vera" is if the draft doesn't.
5. Output ONLY minified JSON: {"body": "...", "cta": "...", "rationale": "..."} \
- cta must be exactly one of: binary_yes_no, multi_choice_slot, open_ended, none. \
Keep the same cta value as the draft unless it's clearly wrong for the body. \
rationale: one sentence on why this message should work, referencing the \
specific trigger/merchant facts used.
No prose outside the JSON."""


def polish(draft_body: str, draft_cta: str, category: dict, merchant: dict,
           trigger: dict, customer: Optional[dict]) -> Optional[dict]:
    if not llm_available():
        return None
    user_payload = {
        "draft_body": draft_body,
        "draft_cta": draft_cta,
        "category_voice": category.get("voice", {}),
        "merchant_identity": merchant.get("identity", {}),
        "merchant_signals": merchant.get("signals", []),
        "trigger_kind": trigger.get("kind"),
        "trigger_payload": trigger.get("payload", {}),
        "customer_identity": (customer or {}).get("identity"),
    }
    body_json = json.dumps({
        "model": MODEL,
        "max_tokens": 500,
        "temperature": 0,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}],
    }).encode("utf-8")

    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=body_json,
        headers={
            "Content-Type": "application/json",
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None  # network / auth / timeout — silently fall back to draft

    try:
        text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        raw = "".join(text_blocks).strip()
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        parsed = json.loads(raw)
        if "body" in parsed and parsed["body"].strip():
            return parsed
    except Exception:
        return None
    return None
