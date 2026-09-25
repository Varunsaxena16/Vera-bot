"""
Rule-based reply handler for /v1/reply.

Order of checks (first match wins):
  1. auto-reply  -> 1st: one short flag message, 2nd: wait 24h, 3rd+: end
  2. hostile / opt-out -> end (and suppress the merchant)
  3. commitment ("yes", "let's do it", "haan karo") -> switch to ACTION immediately
  4. wants time ("later", "busy", "baad mein") -> wait
  5. out-of-scope ask (GST, loans, ...) -> decline in one line, steer back
  6. question -> short honest answer, then the one pending next step
  7. soft no -> polite close
  8. anything else -> acknowledge + advance
"""
from __future__ import annotations

import re

AUTO_PATTERNS = [
    r"thank(s| you) for (contacting|reaching|your message|messaging)",
    r"(will|shall) (get back|respond|reply|revert)", r"our team will",
    r"we('| a)?re (currently )?(closed|away|unavailable)", r"out of (the )?office",
    r"automated (assistant|message|reply)", r"auto[- ]?reply", r"business hours",
    r"respond (shortly|soon|at the earliest)", r"aapki jaankari ke liye", r"hamari team",
    r"jald hi (sampark|jawab)", r"this is an automated",
]
HOSTILE = [
    r"\bstop\b", r"unsubscribe", r"not interested", r"don'?t (message|text|contact|bother)",
    r"\buseless\b", r"\bspam\b", r"bothering", r"leave me alone", r"band karo", r"mat bhejo",
    r"nahi chahiye", r"\bidiot\b", r"\bstupid\b", r"bakwas", r"\bshut up\b", r"\bfraud\b",
    r"\bscam\b", r"never (message|contact)", r"remove (me|my number)", r"\bblock\b",
]
COMMIT = [
    r"^\s*(yes|yess+|yeah|yep|yup|ok|okay|okk+|sure|haan|han|ha|ji|ji haan|done|confirm(ed)?|go|go ahead|proceed|chalega|theek hai|thik hai|kar do|karo|send|please do|do it)\b",
    r"let'?s (do|go|start)", r"lets (do|go|start)", r"\bgo ahead\b", r"\bsounds good\b", r"\bplease (send|share|draft|start|do)\b",
    r"\bkar do\b", r"\bkardo\b", r"\bbhej do\b", r"\bshuru karo\b", r"\bi want to join\b", r"\bjudna hai\b", r"\bjudrna hai\b",
    r"\bsign me up\b", r"\bi'?m in\b", r"^\s*(1|2|3)\s*$",
]
LATER = [r"\blater\b", r"\bbusy\b", r"baad mein", r"\bnot now\b", r"\btomorrow\b", r"\bkal\b", r"call me", r"in a meeting", r"\bnext week\b"]
OFFTOPIC = [r"\bgst\b", r"income tax", r"\bitr\b", r"\bloan\b", r"insurance", r"\bvisa\b", r"electricity bill",
            r"\baccounting\b", r"\bpassport\b", r"\blegal notice\b", r"\bca\b", r"\bpan card\b", r"\baadhaar\b"]
SOFT_NO = [r"^\s*(no|nope|nah|nahi|na|no thanks|not now thanks)\s*[.!]*\s*$", r"\bno thanks\b", r"\bnot required\b", r"\bnot needed\b"]
QUESTION = [r"\?", r"^\s*(what|how|why|when|where|which|who|kya|kaise|kitna|kitne|kab|kyun|kaun)\b", r"\bcost\b", r"\bprice\b", r"\bcharges?\b"]


def _any(pats, text):
    return any(re.search(p, text, flags=re.I) for p in pats)


def classify(msg: str, prev_msgs: list[str]) -> str:
    t = (msg or "").strip()
    low = t.lower()
    if not t:
        return "empty"
    if _any(AUTO_PATTERNS, low) or (prev_msgs and sum(1 for p in prev_msgs if p.strip().lower() == low) >= 1 and len(low) > 25):
        return "auto_reply"
    if _any(HOSTILE, low):
        return "hostile"
    if _any(OFFTOPIC, low):
        return "offtopic"
    if _any(LATER, low) and not re.search(r"let'?s|go ahead|kar do|\byes\b", low):
        return "later"
    if _any(SOFT_NO, low) or (re.search(r"\b(no|nahi|nope|not)\b", low)
                              and not re.search(r"let'?s|go ahead|kar do|\byes\b|no problem|koi baat nahi", low)):
        return "soft_no"
    if _any(COMMIT, low):
        return "commit"
    if _any(QUESTION, low):
        return "question"
    return "other"


def is_hinglish_text(msg: str) -> bool:
    hi_words = r"\b(hai|haan|nahi|kya|karo|kar|aap|mujhe|hum|bhej|chahiye|theek|thik|kaise|kitna|baad|mein|ji|acha|accha)\b"
    return len(re.findall(hi_words, msg or "", flags=re.I)) >= 2


def respond(conv: dict, merchant_msg: str, merchant_state: dict, from_role: str = "merchant") -> dict:
    """
    conv: {offer, kind, stage, bot_bodies, merchant_msgs, name, is_customer}
    merchant_state: per-merchant memory shared across conversations {auto_count, last_auto}
    Returns a /v1/reply response dict.
    """
    offer = conv.get("offer") or "the next step"
    name = conv.get("name") or ""
    hi = is_hinglish_text(merchant_msg)
    kind = classify(merchant_msg, conv.get("merchant_msgs", []) + [merchant_state.get("last_auto", "")])
    customer = conv.get("is_customer") or from_role == "customer"

    if kind == "auto_reply":
        merchant_state["auto_count"] = merchant_state.get("auto_count", 0) + 1
        merchant_state["last_auto"] = (merchant_msg or "").strip().lower()
        n = merchant_state["auto_count"]
        if n == 1:
            body = ("Lagta hai yeh auto-reply hai 🙂 Owner dekhein toh bas YES reply kar dein — main " + offer + " ready rakhungi."
                    if hi else f"Looks like an auto-reply 🙂 When the owner sees this, a simple YES is enough — I'll have {offer} ready.")
            return {"action": "send", "body": body, "cta": "binary_yes_no",
                    "rationale": "Detected WhatsApp Business auto-reply (canned phrasing). One short flag for the owner, then back off."}
        if n == 2:
            return {"action": "wait", "wait_seconds": 86400,
                    "rationale": "Same auto-reply again: owner isn't at the phone. Waiting 24h instead of burning turns."}
        conv["stage"] = "ended"
        return {"action": "end", "rationale": f"Auto-reply {n} times with no human response; closing the conversation to avoid spamming."}

    merchant_state["auto_count"] = 0  # a real human replied

    if kind == "hostile":
        conv["stage"] = "ended"
        merchant_state["opted_out"] = True
        return {"action": "end",
                "rationale": "Merchant asked to stop / expressed frustration. Ending immediately and suppressing further proactive messages to this merchant."}

    if kind == "commit":
        stage = conv.get("stage", "pitched")
        if stage in ("pitched", "answered"):
            conv["stage"] = "drafting"
            if customer:
                body = "Done — booked. We'll send a reminder the day before. See you soon!" if not hi else "Ho gaya — booking confirm. Ek din pehle reminder bhej denge. Milte hain!"
                conv["stage"] = "closing"
                return {"action": "send", "body": body, "cta": "none",
                        "rationale": "Customer confirmed; closing the loop with a confirmation, no further asks."}
            body = (f"Done — kaam shuru kar diya. Draft ({offer}) 10 minute mein yahin bhejti hoon; aapke CONFIRM ke bina kuch live nahi hoga."
                    if hi else f"Done — starting on {offer} now. The draft will be here in about 10 minutes, and nothing goes live until you reply CONFIRM.")
            return {"action": "send", "body": body, "cta": "binary_confirm_cancel",
                    "rationale": "Explicit commitment detected: switched straight to action (no more qualifying questions); next step is a single CONFIRM."}
        if stage == "drafting":
            conv["stage"] = "closing"
            body = ("Confirmed ✅ Live kar diya. 7 din baad results share karungi." if hi
                    else "Confirmed ✅ It's live. I'll share how it performed in 7 days.")
            return {"action": "send", "body": body, "cta": "none",
                    "rationale": "Merchant confirmed the draft; executing and setting a result check-in instead of pitching more."}
        conv["stage"] = "ended"
        return {"action": "end", "rationale": "Task completed and confirmed; nothing further needed, closing gracefully."}

    if kind == "later":
        return {"action": "wait", "wait_seconds": 3600 * 4,
                "rationale": "Merchant asked for time; backing off 4 hours rather than pushing."}

    if kind == "offtopic":
        body = (f"GST/accounts ke liye aapke CA best rahenge — woh mere scope se bahar hai. Wapas apne kaam par: {offer} ready kar doon? Reply YES."
                if hi else f"That one's best handled by your CA — it's outside what I can help with. Back to where we were: shall I get {offer} ready? Reply YES.")
        conv["stage"] = "answered"
        return {"action": "send", "body": _dedupe(conv, body), "cta": "binary_yes_no",
                "rationale": "Out-of-scope request politely declined in one line; redirected to the original thread with a single YES."}

    if kind == "soft_no":
        conv["stage"] = "ended"
        return {"action": "end", "rationale": "Merchant declined; exiting politely without another pitch."}

    if kind == "question":
        low = merchant_msg.lower()
        if re.search(r"\b(cost|price|charge|paid|free|kitna|paisa)\b", low):
            ans = ("Drafting mere taraf se hai — aap pehle dekh lijiye, phir decide kijiye."
                   if hi else "Drafting it costs you nothing extra — you see it first and decide.")
        elif re.search(r"\b(how|kaise)\b", low):
            ans = ("Simple hai: main draft bhejti hoon, aap check karke CONFIRM karte hain, phir main live kar deti hoon."
                   if hi else "Simple: I send the draft, you check it and reply CONFIRM, then I put it live.")
        elif re.search(r"\b(who are you|kaun)\b", low):
            ans = "Main Vera hoon, magicpin ki assistant." if hi else "I'm Vera, magicpin's merchant assistant."
        else:
            ans = ("Achha sawaal — details draft mein clearly likh dungi taaki aap sab dekh sakein."
                   if hi else "Good question — I'll spell that out in the draft so you can see everything before anything goes live.")
        conv["stage"] = "answered"
        tail = f" {offer.capitalize()} bhej doon? Reply YES." if hi else f" Shall I send {offer}? Reply YES."
        return {"action": "send", "body": _dedupe(conv, ans + tail), "cta": "binary_yes_no",
                "rationale": "Answered the merchant's question briefly and honestly, then restated the single pending next step."}

    if kind == "empty":
        return {"action": "wait", "wait_seconds": 3600, "rationale": "Empty message; waiting for a real reply."}

    # other / neutral
    if conv.get("stage") == "closing":
        conv["stage"] = "ended"
        return {"action": "end", "rationale": "Task already delivered and acknowledged; closing without further asks."}
    if len(conv.get("bot_bodies", [])) >= 4:
        conv["stage"] = "ended"
        return {"action": "end", "rationale": "Several turns without a clear signal; closing gracefully instead of nudging again."}
    conv["stage"] = "answered"
    body = (f"Samajh gayi. Main {offer} ready kar deti hoon — bas YES bol dijiye." if hi
            else f"Got it. I can have {offer} ready for you — just reply YES.")
    return {"action": "send", "body": _dedupe(conv, body), "cta": "binary_yes_no",
            "rationale": "Neutral reply: acknowledged and advanced to the single pending next step."}


def _dedupe(conv, body):
    """Never send the same body twice in one conversation."""
    sent = conv.get("bot_bodies", [])
    if body not in sent:
        return body
    alts = [" (No rush — whenever you're ready.)", " Just reply YES when convenient.", " 🙂"]
    for a in alts:
        if body + a not in sent:
            return body + a
    return body + f" [{len(sent)}]"
