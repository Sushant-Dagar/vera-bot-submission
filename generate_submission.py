"""
generate_submission.py — builds submission.jsonl (challenge-brief.md §7.2)
by calling bot.compose() over the 30 canonical (merchant, trigger[, customer])
pairs in dataset/test_pairs.json.
"""
import json
from pathlib import Path

from bot import compose

DATA = Path("dataset")
OUT = Path("submission.jsonl")


def load(kind, cid):
    return json.loads((DATA / kind / f"{cid}.json").read_text())


def main():
    pairs = json.loads((DATA / "test_pairs.json").read_text())["pairs"]
    lines = []
    for p in pairs:
        trigger = load("triggers", p["trigger_id"])
        merchant = load("merchants", p["merchant_id"])
        category = load("categories", merchant["category_slug"])
        customer = load("customers", p["customer_id"]) if p.get("customer_id") else None

        out = compose(category, merchant, trigger, customer)
        line = {
            "test_id": p["test_id"],
            "body": out["body"],
            "cta": out["cta"],
            "send_as": out["send_as"],
            "suppression_key": out["suppression_key"],
            "rationale": out["rationale"],
        }
        lines.append(line)

    with OUT.open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    print(f"Wrote {len(lines)} lines to {OUT}")


if __name__ == "__main__":
    main()
