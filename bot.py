#!/usr/bin/env python3
"""
Vera challenge bot — zero-dependency HTTP server (Python 3.9+ standard library only).

Run:   python bot.py            (listens on $PORT, default 8080)
Endpoints: GET /v1/healthz, GET /v1/metadata, POST /v1/context, POST /v1/tick,
           POST /v1/reply, POST /v1/teardown
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from composer import compose, parse_date
from replies import respond

START = time.time()
LOCK = threading.RLock()
SCOPES = ("category", "merchant", "customer", "trigger")

# ---------------------------------------------------------------- state ----
STATE: dict = {}


def reset_state():
    STATE.clear()
    STATE.update({
        "ctx": {s: {} for s in SCOPES},   # scope -> context_id -> {"version", "payload"}
        "convs": {},                        # conversation_id -> conversation dict
        "sent_keys": set(),                 # suppression keys already used
        "mstate": {},                       # merchant/customer id -> {auto_count, opted_out, ...}
        "sent_bodies": {},                  # merchant_id -> set(bodies)
    })


reset_state()

METADATA = {
    "team_name": os.environ.get("TEAM_NAME", "Team Vera+"),
    "team_members": [m.strip() for m in os.environ.get("TEAM_MEMBERS", "Your Name").split(",")],
    "model": os.environ.get("MODEL_NAME", "deterministic-rule-composer (no LLM)"),
    "approach": ("Decide-then-write: code picks the one signal that matters per trigger kind "
                 "(20+ kind handlers, peer-gap/delta fallback for unknown kinds), writes from context-only "
                 "templates (no fabrication, deterministic), consent-aware customer sends, "
                 "rule-based reply router (auto-reply, intent, hostile, off-topic)."),
    "contact_email": os.environ.get("CONTACT_EMAIL", "you@example.com"),
    "version": "1.0.0",
    "submitted_at": os.environ.get("SUBMITTED_AT", "2026-04-26T08:00:00Z"),
}


def utcnow_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def payload_of(scope, cid):
    rec = STATE["ctx"][scope].get(cid)
    return rec["payload"] if rec else None


# ------------------------------------------------------------- handlers ----

def h_healthz(_body):
    counts = {s: len(STATE["ctx"][s]) for s in SCOPES}
    return 200, {"status": "ok", "uptime_seconds": int(time.time() - START), "contexts_loaded": counts}


def h_metadata(_body):
    return 200, METADATA


def h_context(body):
    scope = body.get("scope")
    cid = body.get("context_id")
    version = body.get("version")
    payload = body.get("payload")
    if scope not in SCOPES:
        return 400, {"accepted": False, "reason": "invalid_scope", "details": f"scope must be one of {SCOPES}"}
    if not cid or not isinstance(payload, dict):
        return 400, {"accepted": False, "reason": "invalid_payload", "details": "context_id and object payload required"}
    try:
        version = int(version)
    except (TypeError, ValueError):
        return 400, {"accepted": False, "reason": "invalid_version", "details": "version must be an integer"}
    with LOCK:
        cur = STATE["ctx"][scope].get(cid)
        if cur and cur["version"] >= version:
            return 409, {"accepted": False, "reason": "stale_version", "current_version": cur["version"]}
        STATE["ctx"][scope][cid] = {"version": version, "payload": payload}
    return 200, {"accepted": True, "ack_id": f"ack_{cid}_v{version}", "stored_at": utcnow_iso()}


def h_tick(body):
    now = parse_date(body.get("now")) or datetime.now(timezone.utc).date()
    avail = body.get("available_triggers") or []
    actions = []
    with LOCK:
        trig_store = {k: v["payload"] for k, v in STATE["ctx"]["trigger"].items()}
        trigs = [trig_store[t] for t in avail if t in trig_store]
        trigs.sort(key=lambda t: (-(t.get("urgency") or 0), str(t.get("id"))))
        per_merchant = {}
        for t in trigs:
            if len(actions) >= 20:
                break
            try:
                tid = t.get("id")
                sk = t.get("suppression_key") or f"{t.get('kind')}:{t.get('merchant_id')}:{tid}"
                if sk in STATE["sent_keys"]:
                    continue
                mid = t.get("merchant_id") or (t.get("payload") or {}).get("merchant_id")
                merchant = payload_of("merchant", mid)
                if not merchant:
                    continue
                if STATE["mstate"].get(mid, {}).get("opted_out"):
                    continue
                category = payload_of("category", merchant.get("category_slug"))
                if not category:
                    continue
                cust_id = t.get("customer_id")
                customer = payload_of("customer", cust_id) if cust_id else None
                if cust_id and customer is None:
                    continue  # customer context not arrived yet; try on a later tick
                if cust_id and STATE["mstate"].get(cust_id, {}).get("opted_out"):
                    continue
                conv_id = f"conv_{mid}_{tid}" + (f"_{cust_id}" if cust_id else "")
                if conv_id in STATE["convs"]:
                    continue
                if not cust_id:
                    if per_merchant.get(mid, 0) >= 2:
                        continue  # restraint: max 2 merchant-facing sends per merchant per tick
                out = compose(category, merchant, t, customer, now=now, store={"triggers": trig_store})
                bodies = STATE["sent_bodies"].setdefault(mid, set())
                if out["body"] in bodies:
                    continue
                if out["send_as"] == "vera":
                    per_merchant[mid] = per_merchant.get(mid, 0) + 1
                bodies.add(out["body"])
                STATE["sent_keys"].add(sk)
                is_cust = out["send_as"] == "merchant_on_behalf"
                STATE["convs"][conv_id] = {
                    "merchant_id": mid, "customer_id": cust_id if is_cust else None, "trigger_id": tid,
                    "kind": t.get("kind"), "offer": out["offer"], "stage": "pitched",
                    "bot_bodies": [out["body"]], "merchant_msgs": [], "is_customer": is_cust,
                }
                actions.append({
                    "conversation_id": conv_id,
                    "merchant_id": mid,
                    "customer_id": cust_id if is_cust else None,
                    "send_as": out["send_as"],
                    "trigger_id": tid,
                    "template_name": out["template_name"],
                    "template_params": out["template_params"],
                    "body": out["body"],
                    "cta": out["cta"],
                    "suppression_key": out["suppression_key"],
                    "rationale": out["rationale"],
                })
            except Exception:
                traceback.print_exc()
                continue
    return 200, {"actions": actions}


def h_reply(body):
    cid = body.get("conversation_id") or f"conv_adhoc_{int(time.time())}"
    msg = body.get("message") or ""
    role = body.get("from_role") or "merchant"
    with LOCK:
        conv = STATE["convs"].get(cid)
        if conv is None:
            mid = body.get("merchant_id")
            merchant = payload_of("merchant", mid) if mid else None
            biz = ((merchant or {}).get("identity") or {}).get("name")
            conv = {"merchant_id": mid, "customer_id": body.get("customer_id"), "trigger_id": None,
                    "kind": None, "offer": f"a ready-to-review draft for {biz}" if biz else "a ready-to-review draft",
                    "stage": "pitched", "bot_bodies": [], "merchant_msgs": [],
                    "is_customer": role == "customer"}
            STATE["convs"][cid] = conv
        if conv.get("stage") == "ended":
            return 200, {"action": "end", "rationale": "Conversation already closed; not re-engaging."}
        key = (body.get("customer_id") or conv.get("customer_id")) if role == "customer" else (body.get("merchant_id") or conv.get("merchant_id") or cid)
        mstate = STATE["mstate"].setdefault(key or cid, {})
        res = respond(conv, msg, mstate, role)
        conv["merchant_msgs"].append(msg)
        if res.get("action") == "send":
            if not (res.get("body") or "").strip():
                res = {"action": "wait", "wait_seconds": 3600, "rationale": "Nothing useful to add right now."}
            else:
                conv["bot_bodies"].append(res["body"])
        return 200, res


def h_teardown(_body):
    with LOCK:
        reset_state()
    return 200, {"ok": True, "wiped_at": utcnow_iso()}


ROUTES = {
    ("GET", "/"): h_healthz,
    ("GET", "/v1/healthz"): h_healthz,
    ("GET", "/v1/metadata"): h_metadata,
    ("POST", "/v1/context"): h_context,
    ("POST", "/v1/tick"): h_tick,
    ("POST", "/v1/reply"): h_reply,
    ("POST", "/v1/teardown"): h_teardown,
}

SAFE_DEFAULT = {"/v1/tick": {"actions": []},
                "/v1/reply": {"action": "wait", "wait_seconds": 1800, "rationale": "Internal error; backing off safely."}}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method):
        path = self.path.split("?")[0].rstrip("/") or "/"
        fn = ROUTES.get((method, path))
        body = {}
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
                if not isinstance(body, dict):
                    raise ValueError("body must be an object")
            except Exception as e:
                return self._send(400, {"accepted": False, "reason": "malformed_json", "details": str(e)[:200]})
        if fn is None:
            return self._send(404, {"error": "not_found", "path": path})
        try:
            code, obj = fn(body)
        except Exception:
            traceback.print_exc()
            code, obj = 200, SAFE_DEFAULT.get(path, {"status": "error"})
        self._send(code, obj)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def log_message(self, fmt, *args):
        if os.environ.get("QUIET") != "1":
            super().log_message(fmt, *args)


def main():
    port = int(os.environ.get("PORT", "8080"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    print(f"Vera bot listening on 0.0.0.0:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
