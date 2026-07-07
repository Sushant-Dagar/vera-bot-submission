# Vera-Challenge Bot — README

## Approach

The core bet: **a deterministic, fact-grounded rule engine beats a raw LLM
prompt on this rubric**, because the rubric penalizes fabrication hardest
(case-study rule #10: "any of them... caps the case at 5/dimension") and
rewards verifiable specificity hardest (dimension #1, weighted equally with
the other four). An LLM asked to "compose a message from these four JSON
blobs" will occasionally invent a number that *sounds* plausible — a rule
engine that only ever reads fields that exist literally cannot.

**Architecture** (`engine/`):

1. **`facts.py`** — every helper either returns a value that exists in one
   of the four contexts, or `None`. Callers must handle `None` by omitting
   the claim, never by inventing one. This is the mechanism that makes
   "don't fabricate" true by construction rather than by prompt instruction.
2. **`playbooks.py`** — ~20 message-family composers dispatched on
   `trigger["kind"]`, each written directly against the 5-dimension rubric
   and the anti-pattern list (specificity anchor, category voice, single
   CTA, compulsion lever, no invented facts). `send_as` is derived centrally
   (`merchant_on_behalf` iff a `CustomerContext` is present) so it can never
   drift from the brief's rule.
3. **`llm.py`** *(optional)* — if `ANTHROPIC_API_KEY` is set, the playbook's
   draft is sent to Claude for a prose *polish pass only*, under an explicit
   instruction not to add any fact absent from the draft/context. Any
   failure (no key, network error, timeout, bad JSON) silently falls back
   to the deterministic draft — the bot never blocks on LLM availability.
   Set `LLM_MODE=off` to force pure-deterministic mode regardless of key.
4. **`validate.py`** — a cheap regex pass on every outgoing message: strips
   banned preambles, flags multi-CTA phrasing, flags generic "% off" when a
   service+price catalog exists, flags taboo vocabulary, flags verbatim
   repeats, capitalizes sentence fragments. Runs whether or not the LLM
   polish pass fired.
5. **`conversation.py`** — the multi-turn engine behind `/v1/reply` and the
   Phase-4 replay scenarios: auto-reply detection (canned-phrase regex +
   "same text 2-3× in a row" streak → send once, then wait, then end),
   intent-transition detection ("let's do it" → switch straight to action,
   no re-qualifying), hostility/opt-out → graceful end, off-topic → decline
   + redirect back to the live thread.

**Why the engine survives the dataset's own gap**: `generate_dataset.py`
expands 25 hand-written trigger seeds into 100, but 75 of the 100 (and 17 of
the 30 canonical test pairs) are thin placeholders (`{"placeholder": true,
"metric_or_topic": kind}`) with none of the rich fields the seed examples
have. Because every playbook sources its numbers from `MerchantContext` /
`CategoryContext` (always fully populated) rather than depending on
`trigger.payload` being rich, placeholder triggers still produce a
specific, honest message anchored on real merchant data — they degrade to
"still true, slightly less detailed" instead of going generic.

## What's included

| File | Purpose |
|---|---|
| `bot.py` | Required `compose(category, merchant, trigger, customer)` entrypoint |
| `submission.jsonl` | Generated output for the 30 canonical test pairs |
| `conversation_handlers.py` | Optional `respond(state, message)` deliverable |
| `server.py` | The 5 HTTP endpoints from the testing brief (FastAPI) |
| `engine/` | facts.py, playbooks.py, validate.py, llm.py, conversation.py |
| `local_harness.py` | Scripted end-to-end test of the HTTP contract (no LLM key needed) |
| `generate_submission.py` | Regenerates `submission.jsonl` from `dataset/test_pairs.json` |
| `dataset/` | The expanded base dataset (output of the provided `generate_dataset.py`) |
| `Dockerfile`, `Procfile`, `render.yaml`, `requirements.txt` | Deployment |

## Running it

```bash
pip install -r requirements.txt
python3 generate_submission.py          # regenerate submission.jsonl
uvicorn server:app --host 0.0.0.0 --port 8080 &
python3 local_harness.py                # scripted contract test, no API key needed
python3 judge_simulator.py              # the real judge sim — needs an LLM API key of your own
```

By default everything runs with **zero external calls** (no `ANTHROPIC_API_KEY`
needed). To turn on the optional LLM polish pass:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn server:app --port 8080
```

## Deploying to a public URL

`render.yaml` + `Dockerfile` are ready for [Render](https://render.com)
(free tier, connects directly to a GitHub repo, auto-detects the Dockerfile):

1. Push this folder to a GitHub repo.
2. Render → New → Blueprint → point at the repo → it reads `render.yaml`.
3. (Optional) set `ANTHROPIC_API_KEY` in the Render dashboard.
4. Submit the resulting `https://<service>.onrender.com` URL.

The same `Dockerfile` works unmodified on Fly.io, Railway, or any container
host; `Procfile` covers classic buildpack platforms.

## Tradeoffs made

- **Rule engine over pure-LLM-prompt-per-message**: faster (<50ms vs
  multi-second LLM round trips), free to run, and — per the reasoning
  above — structurally immune to the rubric's worst penalty (fabrication).
  The tradeoff is prose variety: a pure LLM composer will phrase things more
  differently message-to-message. The optional polish pass is the answer if
  more natural variation is wanted without giving up the fact-grounding.
- **Regex-based auto-reply / intent / hostility detection** over an LLM
  classifier: deterministic, zero-latency, and the patterns in the brief's
  own examples (canned "Thank you for contacting...", "let's do it", "stop
  messaging me") are narrow enough that regex covers them well. A
  production system would likely blend this with an LLM fallback for
  messages that don't match any pattern.
- **In-memory state**: matches the brief's explicit allowance (§2.1) for a
  60-minute test window; would move to Redis/Postgres for a real deployment.

## What additional context would have helped most

The trigger-payload placeholder gap (above) is the biggest one — richer
placeholder payloads (or simply fewer placeholder triggers) would let every
message hit the same specificity ceiling as the 25 hand-written seeds. A
close second: several trigger kinds (`appointment_tomorrow`, `festival_upcoming`
for placeholder instances, generic `dormant_with_vera`) carry no concrete
detail at all — no time, no festival name — so the engine has to phrase
around the gap honestly (e.g. asking for confirmation rather than stating a
specific time) instead of hitting peak specificity. A richer synthetic
payload for these would let the engine be maximally concrete every time
instead of only on the ~25% of triggers that are fully populated.
