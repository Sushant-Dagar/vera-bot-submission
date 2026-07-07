"""
conversation_handlers.py — optional deliverable (challenge-brief.md §7.4).

    def respond(state: ConversationState, merchant_message: str) -> dict

Thin wrapper around engine/conversation.py's decide_reply, returning the
dict shape the brief expects. server.py uses the same engine directly for
POST /v1/reply so the HTTP surface and this optional module never drift
from each other.
"""
from __future__ import annotations
from typing import Optional

from engine.conversation import ConversationState, decide_reply


def respond(state: ConversationState, merchant_message: str) -> dict:
    decision = decide_reply(state, merchant_message)
    out = {"action": decision.action, "rationale": decision.rationale}
    if decision.action == "send":
        out["body"] = decision.body
        out["cta"] = decision.cta
        state.sent_bodies.append(decision.body)
    elif decision.action == "wait":
        out["wait_seconds"] = decision.wait_seconds
    return out


if __name__ == "__main__":
    s = ConversationState(conversation_id="conv_demo")
    for msg in [
        "Yes please send the abstract. Also draft the patient WhatsApp.",
        "Thank you for contacting us! Our team will respond shortly.",
    ]:
        print(msg, "->", respond(s, msg))
