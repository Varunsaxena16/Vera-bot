#!/usr/bin/env python3
"""Generate submission.jsonl for the 30 canonical test pairs.
Usage: python dataset/generate_dataset.py --seed-dir dataset --out expanded && python make_submission.py
"""
import glob, json
from composer import compose

D = "expanded"
C = {json.load(open(f))["slug"]: json.load(open(f)) for f in glob.glob(f"{D}/categories/*.json")}
def load(sub, key):
    out = {}
    for f in glob.glob(f"{D}/{sub}/*.json"):
        x = json.load(open(f)); out[x[key]] = x
    return out
M, CU, T = load("merchants", "merchant_id"), load("customers", "customer_id"), load("triggers", "id")
pairs = json.load(open(f"{D}/test_pairs.json"))["pairs"]
with open("submission.jsonl", "w") as f:
    for p in pairs:
        t, m = T[p["trigger_id"]], M[p["merchant_id"]]
        c = CU.get(p["customer_id"]) if p.get("customer_id") else None
        r = compose(C[m["category_slug"]], m, t, c, now="2026-04-26", store={"triggers": T})
        f.write(json.dumps({"test_id": p["test_id"], "body": r["body"], "cta": r["cta"], "send_as": r["send_as"],
                            "suppression_key": r["suppression_key"], "rationale": r["rationale"]}, ensure_ascii=False) + "\n")
print(f"wrote submission.jsonl ({len(pairs)} lines)")
