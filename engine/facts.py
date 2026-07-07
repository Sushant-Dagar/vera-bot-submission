"""
facts.py — fact extraction helpers.

Core design principle behind this whole engine (see README): NEVER invent a
number, name, offer, or citation that isn't literally present in one of the
four context dicts. Every helper here either returns a real value pulled from
context, or None. Callers must handle None by omitting the claim, not by
making one up. This is what keeps the "don't fabricate" rule (brief §5.8,
anti-pattern list §11, case-study rule #10) mechanically true rather than
aspirational.
"""
from __future__ import annotations
from datetime import datetime, date
from typing import Any, Optional


def _get(d: Optional[dict], *path, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


# ---------------------------------------------------------------- merchant --

def owner_first_name(merchant: dict) -> Optional[str]:
    name = _get(merchant, "identity", "owner_first_name")
    if name:
        return name
    return None


def merchant_display_name(merchant: dict) -> str:
    return _get(merchant, "identity", "name", default="there")


def salutation(merchant: dict, category: Optional[dict] = None) -> str:
    """Prefer the owner's first name (warmer, matches case-study pattern);
    fall back to the business name."""
    fn = owner_first_name(merchant)
    if fn:
        # dentists/doctors: category voice may prefer "Dr. {first_name}"
        slug = merchant.get("category_slug", "")
        if slug in ("dentists",):
            return f"Dr. {fn}"
        return fn
    return merchant_display_name(merchant)


def merchant_languages(merchant: dict) -> list[str]:
    return _get(merchant, "identity", "languages", default=["en"]) or ["en"]


def wants_hindi_mix(merchant: Optional[dict] = None, customer: Optional[dict] = None) -> bool:
    if customer:
        pref = _get(customer, "identity", "language_pref", default="") or ""
        return "hi" in pref.lower()
    if merchant:
        return "hi" in merchant_languages(merchant)
    return False


def active_offers(merchant: dict) -> list[dict]:
    return [o for o in merchant.get("offers", []) if o.get("status") == "active"]


def find_offer(merchant: dict, *keywords: str) -> Optional[dict]:
    """Find an active offer whose title contains any of the keywords (case-insensitive)."""
    for o in active_offers(merchant):
        title = o.get("title", "").lower()
        if any(k.lower() in title for k in keywords):
            return o
    return None


def performance(merchant: dict) -> dict:
    return merchant.get("performance", {}) or {}


def delta_7d(merchant: dict, metric: str) -> Optional[float]:
    return _get(merchant, "performance", "delta_7d", f"{metric}_pct")


def signals(merchant: dict) -> list[str]:
    return merchant.get("signals", []) or []


def has_signal(merchant: dict, needle: str) -> bool:
    return any(needle in s for s in signals(merchant))


def signal_value(merchant: dict, prefix: str) -> Optional[str]:
    """signals look like 'stale_posts:22d' — pull the value after the colon."""
    for s in signals(merchant):
        if s.startswith(prefix):
            parts = s.split(":", 1)
            if len(parts) == 2:
                return parts[1]
    return None


def customer_aggregate(merchant: dict) -> dict:
    return merchant.get("customer_aggregate", {}) or {}


def last_conversation_topic(merchant: dict) -> Optional[str]:
    hist = merchant.get("conversation_history", []) or []
    if hist:
        return hist[-1].get("body")
    return None


def days_since_last_vera_touch(merchant: dict, now: Optional[datetime] = None) -> Optional[int]:
    hist = merchant.get("conversation_history", []) or []
    if not hist:
        return None
    try:
        ts = hist[-1].get("ts")
        last = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = now or datetime.now(last.tzinfo)
        return (now - last).days
    except Exception:
        return None


# ---------------------------------------------------------------- category --

def digest_item(category: dict, item_id: Optional[str]) -> Optional[dict]:
    if not item_id:
        return None
    for d in category.get("digest", []) or []:
        if d.get("id") == item_id:
            return d
    return None


def content_library(category: dict) -> list[dict]:
    return category.get("patient_content_library", []) or []


def first_content_item(category: dict) -> Optional[dict]:
    items = content_library(category)
    return items[0] if items else None


def peer_stats(category: dict) -> dict:
    return category.get("peer_stats", {}) or {}


def offer_catalog(category: dict) -> list[dict]:
    return category.get("offer_catalog", []) or []


def find_catalog_offer(category: dict, *keywords: str) -> Optional[dict]:
    for o in offer_catalog(category):
        title = o.get("title", "").lower()
        if any(k.lower() in title for k in keywords):
            return o
    return None


def voice_taboos(category: dict) -> list[str]:
    return _get(category, "voice", "vocab_taboo", default=[]) or []


def voice_tone(category: dict) -> str:
    return _get(category, "voice", "tone", default="peer_practical")


def category_display(category: dict) -> str:
    return category.get("display_name", category.get("slug", "your category"))


def seasonal_beat_for(category: dict, month_range_hint: Optional[str] = None) -> Optional[dict]:
    beats = category.get("seasonal_beats", []) or []
    if not beats:
        return None
    if month_range_hint:
        for b in beats:
            if b.get("month_range") == month_range_hint:
                return b
    return beats[0]


# ---------------------------------------------------------------- customer --

def customer_first_name(customer: dict) -> str:
    return _get(customer, "identity", "name", default="there")


def customer_language_pref(customer: dict) -> str:
    return _get(customer, "identity", "language_pref", default="en") or "en"


def months_since(iso_date: Optional[str], now: Optional[date] = None) -> Optional[int]:
    if not iso_date:
        return None
    try:
        d = datetime.fromisoformat(iso_date.replace("Z", "")).date()
    except Exception:
        return None
    now = now or date(2026, 4, 26)  # dataset's "current" reference date
    months = (now.year - d.year) * 12 + (now.month - d.month)
    return max(months, 0)


def customer_relationship(customer: dict) -> dict:
    return customer.get("relationship", {}) or {}


def customer_state(customer: dict) -> str:
    return customer.get("state", "active")


def preferred_slot_hint(customer: dict) -> Optional[str]:
    return _get(customer, "preferences", "preferred_slots") or _get(customer, "preferences", "preferred_time")


def slots_from_payload(trigger_payload: dict) -> list[dict]:
    return trigger_payload.get("available_slots") or trigger_payload.get("next_session_options") or []
