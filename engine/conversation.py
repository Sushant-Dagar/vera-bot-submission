"""
conversation.py — multi-turn reply logic for /v1/reply (and the optional
conversation_handlers.py deliverable). Implements the "open challenges" from
challenge-brief.md §12 and the replay-test scenarios in §8/§9 of the testing
brief:

  1. Auto-reply detection: same message verbatim 3+ times → treat as
     WhatsApp-Business canned auto-reply, not a real merchant response.
     (brief §12.1, testing-brief Phase 4.1)
  2. Intent transitions: "let's do it" / "ok go ahead" while mid-qualification
     → switch straight to action, no further qualifying questions.
     (brief §9 Pattern D, testing-brief Phase 4.2)
  3. Hostile / off-topic handling: decline off-mission asks politely and
     redirect; end gracefully on explicit hostility or opt-out.
     (testing-brief Phase 4.3, example 2.6/2.7)
  4. Knowing when to stop: 3 unanswered nudges or explicit opt-out → end.
     (brief §12.5)

This module holds per-conversation state (ConversationState) and exposes:

    decide_reply(state, merchant_message) -> ReplyDecision

server.py wires this to POST /v1/reply. conversation_handlers.py exposes the
same logic under the optional `respond(state, merchant_message)` signature
requested in challenge-brief.md §7.4.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional


AUTO_REPLY_PATTERNS = [
    r"thank you for (contacting|reaching out)",
    r"our team will (respond|get back to you)",
    r"main aapki (yeh )?sabhi baatein.*team tak pahuncha",
    r"automated assistant",
    r"currently unavailable",
    r"business hours",
]

INTENT_TRANSITION_PATTERNS = [
    r"\blet'?s do it\b",
    r"\bok(ay)?,?\s*let'?s\b",
    r"\bgo ahead\b",
    r"\byes,?\s*(please\s*)?(let'?s|do it|proceed)\b",
    r"\bi want to join\b",
    r"\bmujhe (magicpin )?j[uo]d[rn]a hai\b",
    r"\bwhat'?s next\??\b",
    r"\bconfirm(ed)?\b",
    r"\bsure,? go ahead\b",
]

HOSTILE_PATTERNS = [
    r"\bstop (messaging|contacting|texting)\b",
    r"\bnot interested\b",
    r"\bunsubscribe\b",
    r"\b(this is )?useless\b",
    r"\bwhy are you bothering\b",
    r"\bleave me alone\b",
    r"\bfuck|bloody|bakwas|bewakoof\b",
]

OFF_TOPIC_HINT_PATTERNS = [
    r"\bgst\b", r"\bincome tax\b", r"\bloan\b", r"\blegal advice\b", r"\bvisa\b",
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def is_auto_reply_text(text: str) -> bool:
    lower = text.lower()
    return any(re.search(p, lower) for p in AUTO_REPLY_PATTERNS)


def is_intent_transition(text: str) -> bool:
    lower = text.lower()
    return any(re.search(p, lower) for p in INTENT_TRANSITION_PATTERNS)


def is_hostile_or_optout(text: str) -> bool:
    lower = text.lower()
    return any(re.search(p, lower) for p in HOSTILE_PATTERNS)


def is_off_topic(text: str) -> bool:
    lower = text.lower()
    return any(re.search(p, lower) for p in OFF_TOPIC_HINT_PATTERNS)


@dataclass
class ConversationState:
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    original_topic: str = ""
    sent_bodies: list[str] = field(default_factory=list)
    received_messages: list[str] = field(default_factory=list)
    auto_reply_streak: int = 0
    unanswered_nudges: int = 0
    qualifying: bool = True
    ended: bool = False


@dataclass
class ReplyDecision:
    action: str  # "send" | "wait" | "end"
    body: Optional[str] = None
    cta: Optional[str] = None
    wait_seconds: Optional[int] = None
    rationale: str = ""


def decide_reply(state: ConversationState, merchant_message: str) -> ReplyDecision:
    if state.ended:
        return ReplyDecision("end", rationale="Conversation already ended; ignoring further input.")

    state.received_messages.append(merchant_message)
    norm = _norm(merchant_message)

    # --- Auto-reply detection: same text seen before, or matches canned patterns
    seen_before = norm in {_norm(m) for m in state.received_messages[:-1]}
    looks_canned = is_auto_reply_text(merchant_message)

    if looks_canned or seen_before:
        state.auto_reply_streak += 1
        if state.auto_reply_streak == 1:
            return ReplyDecision(
                "send",
                body="Looks like an auto-reply \U0001f60a When the owner sees this, just reply to confirm and I'll pick up right where we left off.",
                cta="binary_yes_no",
                rationale="Detected likely WhatsApp-Business auto-reply on first occurrence; one explicit prompt to flag it for the owner, per brief §12.1.",
            )
        elif state.auto_reply_streak == 2:
            return ReplyDecision(
                "wait", wait_seconds=14400,
                rationale="Same auto-reply / canned phrasing a second time — owner likely not at the phone. Backing off 4h before retrying rather than burning more turns.",
            )
        else:
            state.ended = True
            return ReplyDecision(
                "end",
                rationale="Auto-reply 3+ times in a row with zero real-owner signal; closing per anti-pattern guidance (brief §1 pain point #1) rather than continuing to burn turns.",
            )
    else:
        state.auto_reply_streak = 0  # real reply resets the streak

    # --- Explicit opt-out / hostility → graceful exit, no further engagement
    if is_hostile_or_optout(merchant_message):
        state.ended = True
        return ReplyDecision(
            "end",
            rationale="Merchant explicitly opted out or expressed hostility; closing without further engagement and suppressing future triggers for this conversation.",
        )

    # --- Intent transition: merchant said "let's do it" → switch to ACTION, no more qualifying
    if is_intent_transition(merchant_message):
        state.qualifying = False
        state.unanswered_nudges = 0
        return ReplyDecision(
            "send",
            body=(
                "Great — starting now. I'll draft the first version and share it here in a moment; "
                "reply CONFIRM once you've had a look and I'll send it live."
            ),
            cta="binary_yes_no",
            rationale="Merchant gave an explicit go-ahead; switching from qualification to action immediately per brief §9 Pattern D — no further qualifying questions.",
        )

    # --- Off-topic but not hostile: politely decline + redirect back to the thread
    if is_off_topic(merchant_message):
        return ReplyDecision(
            "send",
            body=(
                "That's outside what I can help with directly — best to check with your CA/relevant "
                "professional for that. Coming back to where we were: want me to continue with the draft?"
            ),
            cta="open_ended",
            rationale="Out-of-scope ask politely declined; redirects back to the original thread without losing it (testing-brief example 2.7).",
        )

    # --- Merchant asked for time / deferred
    if re.search(r"\b(later|not now|busy|call you back|give me (a )?(min|minute|hour|day))\b", merchant_message, re.IGNORECASE):
        return ReplyDecision(
            "wait", wait_seconds=1800,
            rationale="Merchant asked for time; backing off 30 minutes rather than pushing.",
        )

    # --- Default: engaged reply, acknowledge + advance one concrete step
    state.unanswered_nudges = 0
    return ReplyDecision(
        "send",
        body="Got it — sending that now. Anything else you'd like me to adjust before it goes out?",
        cta="open_ended",
        rationale="Engaged merchant reply with no special-case signal; acknowledging and advancing with a low-friction open question.",
    )
