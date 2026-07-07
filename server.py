"""
server.py — the 5-endpoint HTTP surface defined in challenge-testing-brief.md §2.

    POST /v1/context    — idempotent context push, keyed by (scope, context_id, version)
    POST /v1/tick       — periodic wake-up; bot decides what to proactively send
    POST /v1/reply      — synchronous reply to a merchant/customer message
    GET  /v1/healthz    — liveness probe
    GET  /v1/metadata   — bot identity

Run locally:
    pip install -r requirements.txt
    uvicorn server:app --host 0.0.0.0 --port 8080

State is in-memory (dicts + locks), matching the brief's "storing in memory
is fine; just don't restart between calls" allowance (§2.1). For an actual
60-minute test window this is sufficient; a production deployment would swap
the store for Redis/Postgres without touching the endpoint logic below.
"""
from __future__ import annotations

import os
import time
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bot import compose
from engine.conversation import ConversationState, decide_reply, is_auto_reply_text
from engine.validate import validate_and_repair
from engine import facts as f

app = FastAPI(title="Vera-Challenge Bot")
START_TIME = time.time()
LOCK = threading.RLock()

# ---------------------------------------------------------------- state ----
# (scope, context_id) -> {"version": int, "payload": dict}
contexts: dict[tuple[str, str], dict] = {}

# conversation_id -> ConversationState
conversations: dict[str, ConversationState] = {}

# suppression_key -> True, once an action has been sent for it (dedup across ticks)
sent_suppression_keys: set[str] = set()

# merchant_id / customer_id we've ever sent a first outbound to (WhatsApp 24h
# template-vs-freeform rule, brief §5.1)
ever_contacted: set[str] = set()

# merchant_id/customer_id -> canned-auto-reply streak, independent of
# conversation_id. WhatsApp Business auto-replies can arrive under a fresh
# conversation_id even when they're really the Nth canned reply from the
# same merchant/customer, so we track this cross-conversation too and seed
# a new ConversationState's streak from it (see /v1/reply below).
canned_reply_streaks: dict[str, int] = {}

TEAM_NAME = "Sushant Dagar"
TEAM_MEMBERS = ["Sushant Dagar"]
CONTACT = {
    "github": "https://github.com/Sushant-Dagar",
    "linkedin": "https://www.linkedin.com/in/sushantdagar/",
}


# ------------------------------------------------------------- healthz -----

@app.get("/v1/healthz")
async def healthz():
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    with LOCK:
        for (scope, _cid) in contexts.keys():
            counts[scope] = counts.get(scope, 0) + 1
    return {"status": "ok", "uptime_seconds": int(time.time() - START_TIME), "contexts_loaded": counts}


# ------------------------------------------------------------- metadata ----

@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": TEAM_NAME,
        "team_members": TEAM_MEMBERS,
        "model": "deterministic-playbook-engine (+ optional claude-sonnet-4-6 polish pass, see README)",
        "approach": (
            "Fact-grounded rule engine dispatched on trigger.kind (20 playbook families) as the "
            "primary composer — deterministic, needs no LLM call, cannot fabricate since every claim "
            "traces to a context field. Optional LLM polish pass rewrites prose only, under a "
            "no-new-facts constraint, and silently no-ops without ANTHROPIC_API_KEY. A regex-level "
            "validator checks every message against the anti-pattern list before it's returned."
        ),
        "contact_email": "sushant.dagar@example.com",
        "contact": CONTACT,
        "version": "1.0.0",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }


# -------------------------------------------------------------- context ----

class CtxBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: Optional[str] = None


@app.post("/v1/context")
async def push_context(body: CtxBody):
    if body.scope not in ("category", "merchant", "customer", "trigger"):
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_scope", "details": f"unknown scope '{body.scope}'"})
    key = (body.scope, body.context_id)
    with LOCK:
        cur = contexts.get(key)
        if cur and cur["version"] >= body.version:
            return JSONResponse(status_code=409, content={"accepted": False, "reason": "stale_version", "current_version": cur["version"]})
        contexts[key] = {"version": body.version, "payload": body.payload}
    return {"accepted": True, "ack_id": f"ack_{body.context_id}_v{body.version}",
            "stored_at": datetime.now(timezone.utc).isoformat()}


def _get_ctx(scope: str, context_id: Optional[str]) -> Optional[dict]:
    if not context_id:
        return None
    entry = contexts.get((scope, context_id))
    return entry["payload"] if entry else None


# ----------------------------------------------------------------- tick ----

class TickBody(BaseModel):
    now: str
    available_triggers: list[str] = []


def _conversation_id_for(trigger: dict, merchant_id: str) -> str:
    kind = trigger.get("kind", "trg")
    anchor = trigger.get("customer_id") or merchant_id
    return f"conv_{anchor}_{kind}"


@app.post("/v1/tick")
async def tick(body: TickBody):
    actions = []
    with LOCK:
        for trg_id in body.available_triggers[:20]:
            trigger = _get_ctx("trigger", trg_id)
            if not trigger:
                continue
            suppression_key = trigger.get("suppression_key", trg_id)
            if suppression_key in sent_suppression_keys:
                continue  # already actioned this trigger — restraint over spam (brief §14 FAQ)

            merchant_id = trigger.get("merchant_id")
            merchant = _get_ctx("merchant", merchant_id)
            if not merchant:
                continue
            category = _get_ctx("category", merchant.get("category_slug"))
            if not category:
                continue
            customer_id = trigger.get("customer_id")
            customer = _get_ctx("customer", customer_id) if customer_id else None

            composed = compose(category, merchant, trigger, customer)

            conversation_id = _conversation_id_for(trigger, merchant_id)
            state = conversations.setdefault(conversation_id, ConversationState(
                conversation_id=conversation_id, merchant_id=merchant_id, customer_id=customer_id,
                original_topic=trigger.get("kind", ""),
            ))
            state.sent_bodies.append(composed["body"])

            contact_anchor = customer_id or merchant_id
            is_first_contact = contact_anchor not in ever_contacted
            ever_contacted.add(contact_anchor)
            template_name = f"{composed['send_as']}_{trigger.get('kind')}_v1" if is_first_contact else None

            actions.append({
                "conversation_id": conversation_id,
                "merchant_id": merchant_id,
                "customer_id": customer_id,
                "send_as": composed["send_as"],
                "trigger_id": trg_id,
                "template_name": template_name,
                "template_params": [f.salutation(merchant, category), composed["body"]] if is_first_contact else None,
                "body": composed["body"],
                "cta": composed["cta"],
                "suppression_key": suppression_key,
                "rationale": composed["rationale"],
            })
            sent_suppression_keys.add(suppression_key)
    return {"actions": actions}


# ---------------------------------------------------------------- reply ----

class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: Optional[str] = None
    turn_number: Optional[int] = None


@app.post("/v1/reply")
async def reply(body: ReplyBody):
    with LOCK:
        state = conversations.setdefault(
            body.conversation_id,
            ConversationState(conversation_id=body.conversation_id, merchant_id=body.merchant_id, customer_id=body.customer_id),
        )

        # Cross-conversation canned-reply memory: seed this conversation's
        # streak from any prior streak already tracked for this merchant/
        # customer, so the pattern is still caught even if this message
        # showed up under a brand-new conversation_id.
        anchor = body.customer_id or body.merchant_id or body.conversation_id
        if is_auto_reply_text(body.message):
            state.auto_reply_streak = max(state.auto_reply_streak, canned_reply_streaks.get(anchor, 0))

        decision = decide_reply(state, body.message)
        canned_reply_streaks[anchor] = max(canned_reply_streaks.get(anchor, 0), state.auto_reply_streak)

        out = {"action": decision.action, "rationale": decision.rationale}
        if decision.action == "send":
            text = decision.body
            # anti-repetition guard against this conversation's own history
            category = None
            merchant = _get_ctx("merchant", state.merchant_id) or {}
            if merchant.get("category_slug"):
                category = _get_ctx("category", merchant["category_slug"])
            if category is not None:
                text, warnings = validate_and_repair(text, decision.cta or "open_ended", category, merchant,
                                                       prior_bodies=state.sent_bodies)
                if warnings:
                    out["rationale"] += f" [validator flags: {', '.join(warnings)}]"
            state.sent_bodies.append(text)
            out["body"] = text
            out["cta"] = decision.cta
        elif decision.action == "wait":
            out["wait_seconds"] = decision.wait_seconds
    return out


# -------------------------------------------------------------- teardown ---

@app.post("/v1/teardown")
async def teardown():
    with LOCK:
        contexts.clear()
        conversations.clear()
        sent_suppression_keys.clear()
        ever_contacted.clear()
        canned_reply_streaks.clear()
    return {"status": "wiped"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
