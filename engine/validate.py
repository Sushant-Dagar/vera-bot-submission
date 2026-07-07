"""
validate.py — checks the composed message against challenge-brief.md §11
(anti-patterns) and §5 (constraints) before it goes out. Cheap, regex-level
checks — no LLM call needed — so they run on every message including the
deterministic-fallback path.

Repairs are conservative: we only ever strip/soften, never invent replacement
content (that would risk introducing a fabrication).
"""
from __future__ import annotations
import re
from typing import Optional

BANNED_PREAMBLES = [
    r"^i hope (you'?re|you are) doing well",
    r"^i hope this message finds you well",
    r"^i'?m reaching out (today )?to",
]

MULTI_CTA_PATTERN = re.compile(
    r"reply\s+\w+\s+for\s+.+?,?\s*(reply\s+)?\w+\s+for\s+.+?,?\s*(reply\s+)?\w+\s+for\s+",
    re.IGNORECASE,
)

GENERIC_DISCOUNT_PATTERN = re.compile(r"\bflat\s*\d{1,3}%\s*off\b", re.IGNORECASE)

HINDI_MARKERS = re.compile(
    r"\b(hai|hain|aap|apka|apke|apki|kya|kar|karein|kijiye|namaste|bahut|nahi|acha|accha|yeh|abhi|liye|mein|se|ka|ke|ki)\b",
    re.IGNORECASE,
)


def _has_preamble(text: str) -> bool:
    lower = text.strip().lower()
    return any(re.search(p, lower) for p in BANNED_PREAMBLES)


def _strip_preamble_sentence(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    if sentences and _has_preamble(sentences[0]):
        return " ".join(sentences[1:]).strip() or text
    return text


def capitalize_sentences(text: str) -> str:
    """Capitalize the first letter after sentence boundaries. Playbooks
    compose fragments programmatically (e.g. 'It's been X months... - your
    recall is due.') and can end up with a lowercase word right after a
    period/em-dash; this is a purely cosmetic universal repair."""
    def _cap(m):
        return m.group(1) + m.group(2).upper()
    text = re.sub(r"^(\s*)([a-z])", _cap, text)
    text = re.sub(r"([.!?]\s+|\u2014\s)([a-z])", _cap, text)
    return text


def validate_and_repair(
    body: str,
    cta: str,
    category: dict,
    merchant: dict,
    prior_bodies: Optional[list[str]] = None,
    wants_hindi: bool = False,
) -> tuple[str, list[str]]:
    """Returns (possibly-repaired body, list of warning strings)."""
    warnings: list[str] = []
    text = body.strip()

    # 1. Preamble
    if _has_preamble(text):
        warnings.append("stripped_long_preamble")
        text = _strip_preamble_sentence(text)

    text = capitalize_sentences(text)

    # 2. Multi-CTA
    if MULTI_CTA_PATTERN.search(text):
        warnings.append("multi_cta_detected")

    # 3. Generic discount when category has service+price offers
    catalog = category.get("offer_catalog", [])
    has_service_price = any(o.get("type") == "service_at_price" for o in catalog)
    if has_service_price and GENERIC_DISCOUNT_PATTERN.search(text):
        warnings.append("generic_discount_used_when_service_price_available")

    # 4. Taboo vocabulary
    taboos = category.get("voice", {}).get("vocab_taboo", []) or []
    hit_taboos = [t for t in taboos if t.lower() in text.lower() and "use only when" not in t.lower()]
    if hit_taboos:
        warnings.append(f"taboo_vocab_used:{','.join(hit_taboos)}")

    # 5. Anti-repetition
    if prior_bodies and text.strip() in {b.strip() for b in prior_bodies}:
        warnings.append("verbatim_repeat_of_prior_message")

    # 6. Language mismatch (soft heuristic — only warn, never force-translate,
    #    since mistranslating would be worse than an English message)
    if wants_hindi and not HINDI_MARKERS.search(text):
        warnings.append("expected_hindi_english_mix_not_detected")

    # 7. CTA sanity
    if cta not in ("binary_yes_no", "multi_choice_slot", "open_ended", "none"):
        warnings.append(f"unknown_cta_value:{cta}")

    if not text:
        warnings.append("empty_body")

    return text, warnings
