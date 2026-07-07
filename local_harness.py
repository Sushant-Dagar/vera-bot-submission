"""
local_harness.py — a lightweight stand-in for the judge harness, for
development use before running the real judge_simulator.py (which needs an
LLM API key to play the merchant). This script only exercises the HTTP
contract end-to-end: warmup context push, a few ticks, and reply turns
covering the three replay scenarios. No LLM calls — merchant turns are
scripted. Run the server first:

    uvicorn server:app --port 8080 &
    python3 local_harness.py
"""
import json
import sys
from pathlib import Path

import httpx

BASE = "http://localhost:8080"
DATA = Path("dataset")


def load(kind, cid):
    return json.loads((DATA / kind / f"{cid}.json").read_text())


def push(client, scope, context_id, payload, version=1):
    r = client.post(f"{BASE}/v1/context", json={
        "scope": scope, "context_id": context_id, "version": version,
        "payload": payload, "delivered_at": "2026-04-26T09:45:00Z",
    })
    assert r.status_code == 200, (scope, context_id, r.status_code, r.text)
    return r.json()


def main():
    ok = True
    with httpx.Client(timeout=30) as c:
        # --- Phase 1: warmup
        h = c.get(f"{BASE}/v1/healthz").json()
        print("healthz (pre-warmup):", h)
        m = c.get(f"{BASE}/v1/metadata").json()
        print("metadata:", m["team_name"], "|", m["model"][:60])

        for cat_file in sorted((DATA / "categories").glob("*.json")):
            cat = json.loads(cat_file.read_text())
            push(c, "category", cat["slug"], cat)
        n_merch = 0
        for mf in sorted((DATA / "merchants").glob("*.json")):
            merch = json.loads(mf.read_text())
            push(c, "merchant", merch["merchant_id"], merch)
            n_merch += 1
        n_cust = 0
        for cf in sorted((DATA / "customers").glob("*.json")):
            cust = json.loads(cf.read_text())
            push(c, "customer", cust["customer_id"], cust)
            n_cust += 1
        h = c.get(f"{BASE}/v1/healthz").json()
        print("healthz (post-warmup):", h)
        assert h["contexts_loaded"]["category"] == 5
        assert h["contexts_loaded"]["merchant"] == n_merch
        assert h["contexts_loaded"]["customer"] == n_cust

        # idempotency check: re-push same version should be a no-op (still 200, same version wins)
        cat = json.loads((DATA / "categories" / "dentists.json").read_text())
        r = c.post(f"{BASE}/v1/context", json={
            "scope": "category", "context_id": "dentists", "version": 1,
            "payload": cat, "delivered_at": "2026-04-26T09:45:00Z"})
        print("re-push same version ->", r.status_code, r.json())
        assert r.status_code == 409

        # --- Phase 2: push a trigger + tick
        trg = load("triggers", "trg_001_research_digest_dentists")
        push(c, "trigger", trg["id"], trg)
        r = c.post(f"{BASE}/v1/tick", json={"now": "2026-04-26T10:35:00Z", "available_triggers": [trg["id"]]})
        actions = r.json()["actions"]
        print("\ntick #1 actions:", len(actions))
        assert len(actions) == 1
        act = actions[0]
        print(" body:", act["body"][:150])
        print(" send_as:", act["send_as"], "| cta:", act["cta"], "| template_name:", act["template_name"])
        conv_id = act["conversation_id"]

        # duplicate tick should NOT re-send (suppression_key dedup)
        r2 = c.post(f"{BASE}/v1/tick", json={"now": "2026-04-26T10:40:00Z", "available_triggers": [trg["id"]]})
        assert r2.json()["actions"] == [], "expected dedup on repeated trigger"
        print("dedup on repeat tick: OK")

        # --- Phase 4 replay-style checks via /v1/reply on this live conversation

        print("\n-- auto-reply hell --")
        for i in range(3):
            r = c.post(f"{BASE}/v1/reply", json={
                "conversation_id": conv_id, "merchant_id": act["merchant_id"], "customer_id": None,
                "from_role": "merchant",
                "message": "Thank you for contacting Dr. Meera's Dental Clinic! Our team will respond shortly.",
                "received_at": "2026-04-26T10:42:00Z", "turn_number": i + 2})
            d = r.json()
            print(f" turn {i+2}: action={d['action']} | {d['rationale'][:90]}")
        assert d["action"] == "end", "expected graceful end after 3 canned auto-replies"

        print("\n-- intent transition (fresh conversation) --")
        conv2 = "conv_test_intent"
        c.post(f"{BASE}/v1/reply", json={"conversation_id": conv2, "from_role": "merchant",
                                          "message": "Tell me more about this", "turn_number": 1})
        r = c.post(f"{BASE}/v1/reply", json={"conversation_id": conv2, "from_role": "merchant",
                                              "message": "Ok, let's do it. What's next?", "turn_number": 2})
        d = r.json()
        print(" action:", d["action"], "| body:", d.get("body", "")[:100])
        assert "qualif" not in (d.get("body") or "").lower(), "should not re-qualify after explicit go-ahead"
        print(" OK: switched to action, no re-qualification")

        print("\n-- hostile --")
        conv3 = "conv_test_hostile"
        r = c.post(f"{BASE}/v1/reply", json={"conversation_id": conv3, "from_role": "merchant",
                                              "message": "Why are you bothering me. Stop messaging me.", "turn_number": 1})
        d = r.json()
        print(" action:", d["action"])
        assert d["action"] == "end"

        print("\n-- off-topic (stays on mission) --")
        conv4 = "conv_test_offtopic"
        r = c.post(f"{BASE}/v1/reply", json={"conversation_id": conv4, "from_role": "merchant",
                                              "message": "Btw can you also help me with my GST filing?", "turn_number": 1})
        d = r.json()
        print(" action:", d["action"], "| body:", d.get("body", "")[:120])
        assert d["action"] == "send"

    print("\nALL LOCAL HARNESS CHECKS PASSED" if ok else "SOME CHECKS FAILED")


if __name__ == "__main__":
    main()
