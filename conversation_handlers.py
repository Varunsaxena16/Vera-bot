"""Optional multi-turn entry point required by challenge-brief §7.4 (wraps replies.respond)."""
from replies import respond as _respond


def respond(state: dict, merchant_message: str) -> dict:
    """state: {offer, stage, bot_bodies, merchant_msgs, is_customer}; mutated in place."""
    state.setdefault("bot_bodies", []); state.setdefault("merchant_msgs", [])
    merchant_state = state.setdefault("_merchant_state", {})
    out = _respond(state, merchant_message, merchant_state)
    state["merchant_msgs"].append(merchant_message)
    if out.get("action") == "send":
        state["bot_bodies"].append(out["body"])
    return out
