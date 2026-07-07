"""
playbooks.py — deterministic, fact-grounded composers, one family per group of
TriggerContext.kind values (see challenge-brief.md §4.3 for the full kind list).

Each playbook function has signature:

    (category, merchant, trigger, customer) -> PlaybookResult

PlaybookResult carries body/cta/rationale plus a couple of internal fields the
validator and LLM-polish layer use. Playbooks never invent numbers, offers,
names, or citations — see engine/facts.py's docstring. When a trigger payload
is a thin "placeholder" (see dataset generator; ~2/3 of the expanded triggers
are placeholders), the playbook falls back to always-populated MerchantContext
/ CategoryContext fields (performance, signals, offers, customer_aggregate)
so the message still lands on a real, specific anchor instead of going generic.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from . import facts as f


@dataclass
class PlaybookResult:
    body: str
    cta: str  # "binary_yes_no" | "multi_choice_slot" | "open_ended" | "none"
    rationale: str
    send_as: str  # "vera" | "merchant_on_behalf"
    levers: list[str] = field(default_factory=list)


def _hi(word_en: str, word_hi: str, mix: bool) -> str:
    return word_hi if mix else word_en


# ------------------------------------------------------------ digest family --
# research_digest, cde_opportunity, regulation_change, category_seasonal

def digest_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    kind = trigger["kind"]
    sal = f.salutation(merchant, category)
    mix = f.wants_hindi_mix(merchant=merchant)

    if kind == "regulation_change":
        item = f.digest_item(category, payload.get("top_item_id"))
        deadline = payload.get("deadline_iso", "")[:10]
        if item:
            body = (
                f"{sal}, heads up on a compliance change: {item.get('title')}"
                f" ({item.get('source', 'source not specified')})."
            )
            if item.get("summary"):
                body += f" {item['summary']}"
            if deadline:
                body += f" Deadline to comply: {deadline}."
            body += " Want me to draft a compliance checklist for your practice?"
            levers = ["specificity", "loss_aversion"]
        else:
            body = (
                f"{sal}, a regulation update affecting {f.category_display(category)}"
                f" is in this week's digest"
                + (f", compliance deadline {deadline}." if deadline else ".")
                + " Want the details + a checklist?"
            )
            levers = ["loss_aversion"]
        return PlaybookResult(body, "binary_yes_no",
                               "Compliance/regulation trigger — urgency-forward, single actionable CTA.",
                               "vera", levers)

    if kind == "cde_opportunity":
        item = f.digest_item(category, payload.get("digest_item_id"))
        credits = payload.get("credits")
        fee = payload.get("fee")
        title = item.get("title") if item else "a CDE session for your specialty"
        source = item.get("source") if item else None
        bits = [f"{sal}, {title}"]
        if credits:
            bits.append(f"({credits} CDE credits)")
        if fee:
            bits.append(f"— {fee.replace('_', ' ')}")
        body = " ".join(bits) + "."
        if source:
            body += f" — {source}."
        body += " Want me to send the registration link?"
        return PlaybookResult(body, "binary_yes_no",
                               "External CDE/education opportunity; low-urgency, informational with light CTA.",
                               "vera", ["specificity", "reciprocity"])

    if kind == "category_seasonal":
        trends = payload.get("trends", [])
        season = payload.get("season", "").replace("_", " ")
        if trends:
            top = ", ".join(t.replace("_", " ") for t in trends[:3])
            body = (
                f"{sal}, {season or 'this season'}'s shelf/demand signal for "
                f"{f.category_display(category).lower()}: {top}."
            )
        else:
            beat = f.seasonal_beat_for(category)
            note = beat.get("note") if beat else "a seasonal shift"
            body = f"{sal}, {season or 'this season'} typically brings {note} for {f.category_display(category).lower()}."
        if payload.get("shelf_action_recommended"):
            body += " Worth adjusting stock/promotion mix now, ahead of the curve."
        body += " Want me to draft a post highlighting the in-demand items?"
        return PlaybookResult(body, "open_ended",
                               "Category-level seasonal trend; framed as an early-mover action, not generic advice.",
                               "vera", ["specificity", "loss_aversion"])

    # default: research_digest
    item = f.digest_item(category, payload.get("top_item_id"))
    if item:
        anchor = None
        if f.has_signal(merchant, "high_risk_adult_cohort") and item.get("patient_segment") == "high_risk_adults":
            anchor = "your high-risk adult patients"
        elif item.get("patient_segment"):
            anchor = f"your {item['patient_segment'].replace('_', ' ')} patients"
        title = item.get("title", "")
        source = item.get("source", "")
        extra = []
        if item.get("trial_n"):
            extra.append(f"{item['trial_n']:,}-patient trial")
        detail = " showed " + title if not extra else f" {extra[0]} showed {title}"
        body = f"{sal}, {category.get('slug', 'this').upper() if False else source.split(',')[0]}'s latest issue landed."
        if anchor:
            body += f" One item relevant to {anchor} —{detail}."
        else:
            body += f"{detail.capitalize()}."
        content = f.first_content_item(category)
        if content:
            body += (
                f" Worth a look (2-min abstract). Want me to pull it + send your patients "
                f"\"{content['title']}\" ({content.get('length_seconds', 60)}s, ready to share)? — {source}"
            )
        else:
            body += f" Worth a look (2-min abstract). Want me to pull it + draft a patient-ed WhatsApp you can share? — {source}"
        return PlaybookResult(body, "open_ended",
                               "External research digest; anchored on merchant's patient-cohort signal when available, source-cited.",
                               "vera", ["specificity", "curiosity", "reciprocity"])
    body = (
        f"{sal}, this week's {f.category_display(category)} research digest has a new item — "
        "want me to send the summary?"
    )
    return PlaybookResult(body, "open_ended",
                           "Research digest with no matching id in category digest (thin trigger); kept honest, offered summary rather than inventing content.",
                           "vera", ["curiosity"])


# --------------------------------------------------------- competitive intel --

def competitor_opened_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    sal = f.salutation(merchant, category)
    name = payload.get("competitor_name")
    dist = payload.get("distance_km")
    their_offer = payload.get("their_offer")
    own_offer = f.active_offers(merchant)
    if name and dist:
        body = f"{sal}, {name} opened {dist}km from you"
        if payload.get("opened_date"):
            body += f" ({payload['opened_date']})"
        body += "."
        if their_offer:
            body += f" They're running \"{their_offer}\"."
        if own_offer:
            body += f" You already have \"{own_offer[0]['title']}\" live — want me to make sure it's visible on your GBP before their listing settles in?"
            cta = "binary_yes_no"
        else:
            body += " Want me to help set up a competing offer from your catalog so you're not the only one without a headline price?"
            cta = "open_ended"
        return PlaybookResult(body, cta,
                               "Competitive intel; concrete distance+name, ties to merchant's own offer state rather than generic alarm.",
                               "vera", ["specificity", "loss_aversion"])
    body = (
        f"{sal}, a new {f.category_display(category)} competitor opened near you recently. "
        "Want me to check how your listing compares on price and reviews?"
    )
    return PlaybookResult(body, "open_ended",
                           "Competitor trigger with thin payload; avoided inventing a name/distance.",
                           "vera", ["loss_aversion"])


# ---------------------------------------------------------------- performance --

def performance_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    kind = trigger["kind"]
    sal = f.salutation(merchant, category)
    metric = payload.get("metric", "views")
    delta = payload.get("delta_pct")
    if delta is None:
        delta = f.delta_7d(merchant, metric)
    window = payload.get("window", "7d")

    def pct(x):
        return f"{abs(round(x * 100))}%"

    if kind == "perf_spike":
        driver = payload.get("likely_driver", "").replace("_", " ")
        baseline = payload.get("vs_baseline")
        body = f"{sal}, your {metric} are up {pct(delta) if delta else 'noticeably'} over the last {window}"
        if baseline:
            body += f" (vs your usual ~{baseline}/day)"
        body += "."
        if driver:
            body += f" Looks tied to {driver}."
        body += " Want me to double down — a follow-up post while it's hot?"
        return PlaybookResult(body, "binary_yes_no",
                               "Performance spike; cites metric+window+baseline, offers to capitalize.",
                               "vera", ["specificity", "reciprocity"])

    if kind in ("perf_dip", "seasonal_perf_dip"):
        seasonal = payload.get("is_expected_seasonal")
        note = payload.get("season_note", "").replace("_", " ")
        body = f"{sal}, your {metric} are down {pct(delta) if delta else 'noticeably'} over the last {window}"
        if seasonal:
            peer = f.peer_stats(category)
            body += (
                f" — but this looks like the {note or 'normal seasonal'} dip most "
                f"{f.category_display(category).lower()} see this time of year, not something specific to you."
            )
            body += " Suggest holding ad spend rather than reacting; want me to flag when the window closes?"
            cta = "open_ended"
            levers = ["reciprocity", "specificity"]
        else:
            body += ". Want me to pull the last 3 changes to your profile/offers to see what might be driving it?"
            cta = "binary_yes_no"
            levers = ["loss_aversion", "specificity"]
        return PlaybookResult(body, cta,
                               "Performance dip; distinguishes seasonal-expected vs anomalous using trigger flag, avoids alarming merchant unnecessarily.",
                               "vera", levers)

    # generic fallback
    body = f"{sal}, noticed a shift in your {metric} recently — want me to take a look?"
    return PlaybookResult(body, "open_ended", "Generic performance trigger fallback.", "vera", ["curiosity"])


# ------------------------------------------------------------------ milestone --

def milestone_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    sal = f.salutation(merchant, category)
    metric = payload.get("metric")
    value_now = payload.get("value_now")
    milestone_value = payload.get("milestone_value")
    METRIC_LABELS = {
        "review_count": "reviews",
        "views": "views",
        "calls": "calls",
        "directions": "direction requests",
        "leads": "leads",
    }
    if metric and value_now and milestone_value:
        remaining = milestone_value - value_now
        metric_label = METRIC_LABELS.get(metric, metric.replace("_", " "))
        if payload.get("is_imminent") and remaining > 0:
            body = (
                f"{sal}, you're at {value_now} {metric_label} — just {remaining} away from "
                f"{milestone_value}. Want me to draft a \"help us hit {milestone_value}\" nudge for your regulars?"
            )
        else:
            body = f"{sal}, you crossed {value_now} {metric_label}! Want a Google post to mark it?"
        return PlaybookResult(body, "binary_yes_no",
                               "Milestone trigger with real counts from payload; loss-aversion-style near-miss framing when imminent.",
                               "vera", ["specificity", "loss_aversion" if payload.get("is_imminent") else "reciprocity"])
    agg = f.customer_aggregate(merchant)
    if agg.get("total_unique_ytd"):
        body = (
            f"{sal}, you've served {agg['total_unique_ytd']} unique customers this year so far. "
            "Want me to turn that into a Google post?"
        )
        return PlaybookResult(body, "binary_yes_no",
                               "Milestone trigger with thin payload; fell back to customer_aggregate as the real, specific number.",
                               "vera", ["specificity"])
    body = f"{sal}, you hit a milestone worth celebrating — want me to check the numbers and draft a post?"
    return PlaybookResult(body, "open_ended", "Milestone trigger with no usable numeric payload; kept honest.", "vera", [])


# --------------------------------------------------------- engagement cadence --

def curious_ask_family(category, merchant, trigger, customer) -> PlaybookResult:
    sal = f.salutation(merchant, category)
    body = (
        f"Hi {sal}! Quick check — what's been the most-asked-for service at your place this week? "
        "I'll turn the answer into a Google post + a short WhatsApp reply you can reuse. Takes 5 min."
    )
    return PlaybookResult(body, "open_ended",
                           "Weekly curious-ask cadence; asking-the-merchant lever (brief §10.7), low-friction, offers a concrete deliverable in return.",
                           "vera", ["asking_the_merchant", "effort_externalization", "reciprocity"])


def dormant_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    sal = f.salutation(merchant, category)
    days = payload.get("days_since_last_merchant_message") or f.days_since_last_vera_touch(merchant)
    topic = payload.get("last_topic", "").replace("_", " ")
    stale = f.signal_value(merchant, "stale_posts")
    bits = [f"Hi {sal}, haven't heard from you"]
    if days:
        bits[0] += f" in {days} days"
    bits[0] += "."
    if topic:
        bits.append(f"Last we spoke it was about {topic} — did that get sorted?")
    elif stale:
        bits.append(f"Your last Google post was {stale} ago — want me to draft a fresh one now?")
    else:
        bits.append("Anything I can help unblock — profile, offers, or a quick post?")
    body = " ".join(bits)
    return PlaybookResult(body, "open_ended",
                           "Re-engagement after dormancy; references the actual last topic or a real stale_posts signal rather than a generic 'are you there?'.",
                           "vera", ["reciprocity"])


# ---------------------------------------------------------------- external event --

def festival_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    sal = f.salutation(merchant, category)
    festival = payload.get("festival", "the upcoming festival")
    days_until = payload.get("days_until")
    offer = f.active_offers(merchant)
    body = f"{sal}, {festival} is"
    if days_until is not None:
        body += f" {days_until} days away."
    else:
        body += " coming up."
    if offer:
        body += f" Your \"{offer[0]['title']}\" offer is a natural fit to headline for it — want me to build a {festival} post around it?"
        cta = "binary_yes_no"
    else:
        catalog_offer = f.find_catalog_offer(category, festival.lower())
        body += " Want me to draft a festival-specific offer from your catalog for it?"
        cta = "open_ended"
    return PlaybookResult(body, cta,
                           "Upcoming festival; ties to merchant's own live offer when one exists rather than suggesting a generic discount.",
                           "vera", ["specificity", "effort_externalization"])


def ipl_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    sal = f.salutation(merchant, category)
    match = payload.get("match")
    venue = payload.get("venue")
    is_weeknight = payload.get("is_weeknight")
    offer = f.active_offers(merchant)
    body = f"Quick heads-up {sal} — {match} at {venue} tonight."
    if is_weeknight is False:
        body += " Heads up: weekend IPL nights usually mean lower dine-in/delivery covers (people watch at home) vs weeknight matches."
        if offer:
            body += f" Might be better to lean on your \"{offer[0]['title']}\" as a delivery-only special tonight rather than a match-day dine-in push."
        levers = ["specificity", "reciprocity"]
    else:
        body += " Weeknight IPL matches usually mean a spike in delivery orders."
        if offer:
            body += f" Want me to push your \"{offer[0]['title']}\" as tonight's match-night special?"
        levers = ["specificity", "loss_aversion"]
    body += " Want me to draft the banner? Live in 10 min."
    return PlaybookResult(body, "binary_yes_no",
                           "IPL match-day trigger; contrarian data-informed read on weeknight vs weekend behaviour (case-study 5 pattern), not a blanket promo push.",
                           "vera", levers)


# ------------------------------------------------------------------ subscription --

def renewal_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    sal = f.salutation(merchant, category)
    days = payload.get("days_remaining", f.performance(merchant))
    days = payload.get("days_remaining") or _get_sub_days(merchant)
    plan = payload.get("plan") or _get(merchant, "subscription", "plan")
    amount = payload.get("renewal_amount")
    body = f"{sal}, your {plan or ''} plan renews in {days} days" if days else f"{sal}, your plan is coming up for renewal"
    if amount:
        body += f" (₹{amount:,})".replace(",", ",")
    body += ". "
    agg = f.customer_aggregate(merchant)
    if agg.get("total_unique_ytd"):
        body += f"You've served {agg['total_unique_ytd']} customers through the platform this year — "
    body += "want me to lock in the renewal now so there's no visibility gap?"
    return PlaybookResult(body, "binary_yes_no",
                           "Subscription renewal; cites real days-remaining/amount, ties continuity to a real usage number when available.",
                           "vera", ["loss_aversion", "specificity"])


def _get_sub_days(merchant):
    return _get(merchant, "subscription", "days_remaining")


def _get(d, *path, default=None):
    return f._get(d, *path, default=default)


# ----------------------------------------------------------- compliance alert --

def supply_alert_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    sal = f.salutation(merchant, category)
    molecule = payload.get("molecule")
    batches = payload.get("affected_batches", [])
    mfr = payload.get("manufacturer")
    if molecule and batches:
        body = f"{sal}, urgent: voluntary recall on {len(batches)} {molecule} batch(es) ({', '.join(batches)})"
        if mfr:
            body += f" by {mfr}"
        body += ". Customers dispensed these should be offered a replacement."
        chronic = f.customer_aggregate(merchant).get("total_unique_ytd")
        if chronic:
            body += f" Want me to cross-check against your dispensing records (you've got {chronic} regular customers on file) and draft the customer note + replacement workflow?"
        else:
            body += " Want me to draft the customer note + replacement workflow?"
        return PlaybookResult(body, "binary_yes_no",
                               "Supply/compliance alert; batch numbers + manufacturer cited exactly, offered workflow without inventing an affected-customer count not present in context.",
                               "vera", ["specificity", "loss_aversion"])
    body = f"{sal}, there's a supply/compliance alert relevant to your stock this week — want the details?"
    return PlaybookResult(body, "open_ended", "Supply alert with thin payload; kept honest.", "vera", [])


def gbp_unverified_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    sal = f.salutation(merchant, category)
    uplift = payload.get("estimated_uplift_pct")
    path = payload.get("verification_path", "postcard or phone call")
    body = f"{sal}, your Google Business Profile is still unverified"
    if uplift:
        body += f" — verified listings in {f.category_display(category).lower()} typically see ~{round(uplift*100)}% more visibility"
    body += f". Verification is quick ({path.replace('_', ' ')}). Want me to start it for you?"
    return PlaybookResult(body, "binary_yes_no",
                           "Unverified-GBP trigger; cites the real estimated uplift figure from payload rather than a made-up one.",
                           "vera", ["loss_aversion", "specificity"])


# ------------------------------------------------------------------ review theme --

def review_theme_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    sal = f.salutation(merchant, category)
    theme = payload.get("theme", "").replace("_", " ")
    occ = payload.get("occurrences_30d")
    trend = payload.get("trend")
    quote = payload.get("common_quote")
    if theme and occ:
        body = f"{sal}, {occ} reviews in the last 30 days mention \"{theme}\""
        if trend == "rising":
            body += " — and it's trending up"
        body += "."
        if quote:
            body += f" One reads: \"{quote[:60]}\"." if len(quote) <= 60 else ""
        body += " Want me to draft a response template + flag it as a fix priority?"
        return PlaybookResult(body, "binary_yes_no",
                               "Review theme; cites real occurrence count + trend direction, offers concrete next step.",
                               "vera", ["specificity", "loss_aversion"])
    body = f"{sal}, a theme is emerging in your recent reviews — want me to summarize it for you?"
    return PlaybookResult(body, "open_ended", "Review-theme trigger with thin payload.", "vera", [])


# ------------------------------------------------------------- active planning --

def active_planning_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    sal = f.salutation(merchant, category)
    topic = payload.get("intent_topic", "").replace("_", " ")
    last_msg = payload.get("merchant_last_message", "")
    body = (
        f"Great — building on the {topic} idea. "
        f"Give me the headline details (price point, what's included, who it's for) and I'll turn it into "
        f"a GBP post + a WhatsApp blurb you can send customers directly. What should the price/package look like?"
    )
    return PlaybookResult(body, "open_ended",
                           "Merchant is mid-planning (explicit continuation signal in trigger payload); moving straight to execution questions rather than re-qualifying (avoids the intent-handoff failure pattern, brief §9 Pattern D).",
                           "vera", ["asking_the_merchant", "effort_externalization"])


# ------------------------------------------------------------ merchant winback --

def winback_eligible_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    sal = f.salutation(merchant, category)
    lapsed = payload.get("lapsed_customers_added_since_expiry")
    days_since_expiry = payload.get("days_since_expiry")
    body = f"{sal}, "
    if lapsed:
        body += f"{lapsed} of your customers have gone quiet since your subscription lapsed"
        if days_since_expiry:
            body += f" {days_since_expiry} days ago"
        body += "."
    else:
        body += "a chunk of your customer base has gone quiet since your subscription lapsed."
    body += " Reactivating now typically recovers the biggest share before they settle with someone else. Want me to draft a winback offer for that list?"
    return PlaybookResult(body, "binary_yes_no",
                           "Merchant-scope winback trigger about a lapsed-customer cohort; cites real cohort size from payload.",
                           "vera", ["loss_aversion", "specificity"])


# ============================================================ CUSTOMER-FACING =
# recall_due, chronic_refill_due, customer_lapsed_soft, customer_lapsed_hard,
# appointment_tomorrow, trial_followup, wedding_package_followup

def _customer_greeting(customer: dict, mix: bool) -> str:
    name = f.customer_first_name(customer)
    return f"Hi {name}"


def recall_due_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    mix = f.wants_hindi_mix(customer=customer)
    greet = _customer_greeting(customer, mix)
    mname = f.merchant_display_name(merchant)
    service_raw = payload.get("service_due", "").replace("_", " ")
    service_label = f"{service_raw} recall" if service_raw else "recall"
    last_service = payload.get("last_service_date")
    months = f.months_since(last_service)
    slots = f.slots_from_payload(payload)
    offer = f.find_offer(merchant, "clean") or (f.active_offers(merchant)[0] if f.active_offers(merchant) else None)

    body = f"{greet}, {mname} here"
    body += " \U0001f9b7" if "dent" in merchant.get("category_slug", "") else ""
    body += "."
    if months is not None and months > 0:
        body += f" It's been {months} months since your last visit"
        if mix:
            body += f" — aapka {service_label} due hai."
        else:
            body += f" — your {service_label} is due."
    else:
        body += f" Your {service_label} is due."
    if slots:
        if mix:
            body += f" Apke liye {len(slots)} slots ready hain: " + " ya ".join(s["label"] for s in slots) + "."
        else:
            body += " Available slots: " + " or ".join(s["label"] for s in slots) + "."
    if offer:
        body += f" {offer['title']}"
        if "fluoride" in str(category.get("offer_catalog", [])).lower() and "dent" in merchant.get("category_slug", ""):
            pass
        body += "."
    if len(slots) > 1:
        body += " Reply 1 for the first, 2 for the second, or tell us a time that works."
    elif len(slots) == 1:
        body += " Reply YES to confirm, or tell us a time that works."
    else:
        body += " Reply YES to book, or tell us a time that works."
    cta = "multi_choice_slot" if slots else "binary_yes_no"
    return PlaybookResult(body, cta,
                           "Customer recall reminder; real slots from trigger payload, real active offer from merchant catalog, language pref honored, sent as merchant.",
                           "merchant_on_behalf", ["specificity", "single_binary_commitment"])


def chronic_refill_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    mix = f.wants_hindi_mix(customer=customer)
    name = f.customer_first_name(customer)
    mname = f.merchant_display_name(merchant)
    molecules = payload.get("molecule_list", [])
    runs_out = (payload.get("stock_runs_out_iso") or "")[:10]
    delivery_saved = payload.get("delivery_address_saved")
    delivery_offer = f.find_offer(merchant, "delivery") or f.find_catalog_offer(category, "delivery")
    senior_offer = f.find_offer(merchant, "senior") or f.find_catalog_offer(category, "senior")

    if mix:
        body = f"Namaste — {mname} yahan. "
    else:
        body = f"Hi {name}, {mname} here. "
    if molecules:
        mol_str = ", ".join(molecules)
        body += f"Your regular medicines ({mol_str}) will run out {'around ' + runs_out if runs_out else 'soon'}. Same dose, same brand — ready to prepare."
    elif runs_out:
        body += f"Your regular refill is due around {runs_out}."
    else:
        body += "Your regular refill looks due soon based on your last pickup."
    if senior_offer:
        body += f" {senior_offer['title']} applies if eligible."
    if delivery_saved and delivery_offer:
        body += f" {delivery_offer['title']} to your saved address."
    elif delivery_offer:
        body += f" {delivery_offer['title']} available."
    body += " Reply CONFIRM to dispatch, or call if anything's changed."
    return PlaybookResult(body, "binary_yes_no",
                           "Chronic refill reminder; molecule list + real due date from trigger payload, real offers from merchant/category catalog, no invented totals/prices.",
                           "merchant_on_behalf", ["specificity", "single_binary_commitment"])


def customer_lapsed_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    kind = trigger["kind"]
    name = f.customer_first_name(customer)
    mname = f.merchant_display_name(merchant)
    owner = f.owner_first_name(merchant)
    rel = f.customer_relationship(customer)
    days_since = payload.get("days_since_last_visit")
    last_visit = rel.get("last_visit")
    if not days_since and last_visit:
        m = f.months_since(last_visit)
        days_since = m * 30 if m else None
    focus = payload.get("previous_focus", "").replace("_", " ")
    offer = f.active_offers(merchant)
    frm = f"{owner} from {mname}" if owner else mname
    body = f"Hi {name} \U0001f44b {frm} here. "
    if days_since:
        weeks = round(days_since / 7)
        body += f"It's been about {weeks} weeks — happens to most of us at some point, no judgment. "
    else:
        body += "It's been a little while — no judgment. "
    if focus:
        body += f"Still thinking about your {focus} goals? "
    if offer:
        body += f"We've got \"{offer[0]['title']}\" live right now if you want a low-pressure way back in. "
    body += "Want me to hold a spot for you this week? Reply YES — no commitment."
    return PlaybookResult(body, "binary_yes_no",
                           f"{kind} — warm no-shame winback tone (customer-facing voice rule), references real previous focus + real live offer.",
                           "merchant_on_behalf", ["specificity", "single_binary_commitment"])


def appointment_tomorrow_family(category, merchant, trigger, customer) -> PlaybookResult:
    name = f.customer_first_name(customer)
    mname = f.merchant_display_name(merchant)
    mix = f.wants_hindi_mix(customer=customer)
    if mix:
        body = f"Hi {name}, {mname} se ek reminder — aapki appointment kal hai. Confirm karne ke liye reply YES, ya reschedule ke liye batayein."
    else:
        body = f"Hi {name}, quick reminder from {mname} — you have an appointment tomorrow. Reply YES to confirm, or let us know if you need to reschedule."
    return PlaybookResult(body, "binary_yes_no",
                           "Appointment reminder; trigger payload is a thin placeholder (no exact time in context) so the message stays honest and asks for confirmation rather than inventing a time slot.",
                           "merchant_on_behalf", ["single_binary_commitment"])


def trial_followup_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    name = f.customer_first_name(customer)
    mname = f.merchant_display_name(merchant)
    options = f.slots_from_payload(payload)
    trial_date = payload.get("trial_date")
    body = f"Hi {name}, {mname} here. "
    if trial_date:
        body += f"Hope the trial session on {trial_date} went well! "
    if options:
        body += "Next slot available: " + ", ".join(o["label"] for o in options) + ". "
        body += "Want me to book it for you? Reply YES to confirm."
        cta = "binary_yes_no"
    else:
        body += "Want to lock in your next session? Let us know a time that works."
        cta = "open_ended"
    return PlaybookResult(body, cta,
                           "Post-trial followup; real trial date + real next-slot option from trigger payload.",
                           "merchant_on_behalf", ["specificity", "single_binary_commitment"])


def wedding_followup_family(category, merchant, trigger, customer) -> PlaybookResult:
    payload = trigger.get("payload", {})
    name = f.customer_first_name(customer)
    owner = f.owner_first_name(merchant)
    mname = f.merchant_display_name(merchant)
    days_to = payload.get("days_to_wedding")
    program = payload.get("next_step_window_open", "").replace("_", " ")
    offer = f.find_offer(merchant, program.split()[0] if program else "prep") or f.find_catalog_offer(category, "bridal")
    frm = f"{owner} from {mname}" if owner else mname
    body = f"Hi {name} \U0001f48d {frm} here."
    if days_to is not None:
        body += f" {days_to} days to your wedding"
        if program:
            body += f" — good window to start the {program}."
        body += " "
    if offer:
        body += f"{offer['title']} — "
        body += "want me to block your preferred slot for the first session?"
    else:
        body += "Want me to put together a plan and available slots for you?"
    return PlaybookResult(body, "binary_yes_no",
                           "Bridal-followup customer message; real days-to-wedding + program name from trigger payload; offer taken from merchant's real catalog only if present (never invented a price).",
                           "merchant_on_behalf", ["specificity", "single_binary_commitment"])


# ------------------------------------------------------------------ dispatch --

FAMILY_MAP = {
    "research_digest": digest_family,
    "cde_opportunity": digest_family,
    "regulation_change": digest_family,
    "category_seasonal": digest_family,
    "competitor_opened": competitor_opened_family,
    "perf_spike": performance_family,
    "perf_dip": performance_family,
    "seasonal_perf_dip": performance_family,
    "milestone_reached": milestone_family,
    "curious_ask_due": curious_ask_family,
    "scheduled_recurring": curious_ask_family,
    "dormant_with_vera": dormant_family,
    "festival_upcoming": festival_family,
    "ipl_match_today": ipl_family,
    "renewal_due": renewal_family,
    "supply_alert": supply_alert_family,
    "gbp_unverified": gbp_unverified_family,
    "review_theme_emerged": review_theme_family,
    "active_planning_intent": active_planning_family,
    "winback_eligible": winback_eligible_family,
    # customer-facing
    "recall_due": recall_due_family,
    "chronic_refill_due": chronic_refill_family,
    "customer_lapsed_soft": customer_lapsed_family,
    "customer_lapsed_hard": customer_lapsed_family,
    "appointment_tomorrow": appointment_tomorrow_family,
    "trial_followup": trial_followup_family,
    "wedding_package_followup": wedding_followup_family,
}


def compose_playbook(category: dict, merchant: dict, trigger: dict, customer: Optional[dict]) -> PlaybookResult:
    kind = trigger.get("kind", "")
    fn = FAMILY_MAP.get(kind)
    if fn is None:
        # Unseen kind — safe generic fallback, still merchant-anchored.
        sal = f.salutation(merchant, category)
        body = f"{sal}, quick update relevant to your business — want the details?"
        return PlaybookResult(body, "open_ended", f"Unrecognized trigger kind '{kind}'; generic-but-safe fallback.", "vera" if not customer else "merchant_on_behalf", [])
    result = fn(category, merchant, trigger, customer)
    # Enforce send_as correctness centrally: customer-facing iff CustomerContext present.
    result.send_as = "merchant_on_behalf" if customer else "vera"
    return result
