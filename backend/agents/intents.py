"""Rule-based intent extraction for Phase 1.

Phase 3 replaces extract_intent with an LLM producing the same structured
backend.schemas.intents.Intent model. Only the user's message is ever inspected —
product titles, descriptions and reviews are untrusted DATA and never reach this.
"""

import re

from backend.schemas.intents import Intent, IntentType

_APPROVE_WORDS = {
    "approve", "approved", "confirm", "confirmed", "yes", "yeah", "yep", "sure",
    "go ahead", "proceed", "place the order", "place order", "ok approve", "do it",
}
# "ok"/"maybe" alone are NOT approval — they get a nudge instead.
_AMBIGUOUS_WORDS = {"ok", "okay", "maybe", "hmm", "idk", "not sure"}
_REJECT_WORDS = {
    "reject", "rejected", "no", "nope", "cancel", "cancel it", "stop",
    "discard", "don't", "dont", "do not", "nevermind", "never mind",
}

_ORDINALS = {
    "first": 0, "1st": 0, "one": 0, "1": 0,
    "second": 1, "2nd": 1, "two": 1, "2": 1,
    "third": 2, "3rd": 2, "three": 2, "3": 2,
}


def extract_intent(message: str, session: dict, role: str = "buyer") -> Intent:
    msg = (message or "").strip().lower()
    pending = bool(session.get("pending_proposal_id"))

    if pending and msg in _APPROVE_WORDS:
        return Intent(type=IntentType.APPROVE, mode="any", params={})
    if pending and msg in _AMBIGUOUS_WORDS:
        return Intent(type=IntentType.CLARIFY, mode="any", params={"hint": "pending_proposal"})
    if pending and msg in _REJECT_WORDS:
        return Intent(type=IntentType.REJECT, mode="any", params={})

    if (
        re.search(r"\b(list|sell|create|add|publish)\b.*\b(listing|product|item)\b", msg)
        or re.match(r"^(list|sell)\b", msg)
        or re.search(r"i want to (sell|list)", msg)
        or re.search(r"new listing", msg)
    ):
        return Intent(type=IntentType.SELLER_LISTING, mode="seller", params=_extract_listing_fields(msg))

    if re.search(
        r"order\s+(status|update)|track(ing)?\s+(my\s+)?order|where is my order|status of (my|the) order",
        msg,
    ):
        return Intent(type=IntentType.ORDER_STATUS, mode="buyer", params={})

    if re.search(r"sales summary|my sales|sales report|how much did i sell|how many did i sell", msg):
        return Intent(type=IntentType.SALES_SUMMARY, mode="seller", params={})

    if re.search(r"\bstock\b", msg) and re.search(r"\b(check|how many|availability|available)\b", msg):
        q = re.sub(r".*\bstock\b\s*(of|for)?\s*", "", msg).strip(" ?!.")
        return Intent(type=IntentType.STOCK_CHECK, mode="seller", params={"query": q})

    buy_m = re.search(r"\b(buy|purchase|order)\s+(.+)", msg)
    if buy_m:
        rest = buy_m.group(2).strip(" .!")
        qty_m = re.search(r"^(\d+)\s*(?:x|pcs|pieces|units)?\s*", rest)
        quantity = int(qty_m.group(1)) if qty_m else 1
        if qty_m:
            rest = rest[qty_m.end():].strip(" .!")
        target = None
        for word in _ORDINALS:
            if re.search(rf"\b{word}\b", rest):
                target = word
                break
        if target is None:
            if re.search(r"\b(it|this|that|the one)\b", rest):
                target = "last"
            else:
                target = rest or "last"
        return Intent(type=IntentType.BUY, mode="buyer", params={"target": target, "quantity": quantity})

    if re.search(r"\b(find|search|looking for|show me|recommend|browse|compare)\b", msg) or re.search(
        r"\bunder\s+(pkr|rs)\b", msg
    ):
        query, max_price = _extract_search(msg)
        return Intent(
            type=IntentType.SEARCH,
            mode="buyer",
            params={"query": query, "max_price_pkr": max_price},
        )

    if re.search(r"\b(hi|hello|hey|salam|assalam|help|start)\b", msg):
        return Intent(type=IntentType.HELP, mode="buyer" if role != "seller" else "seller", params={})

    return Intent(type=IntentType.CLARIFY, mode="buyer" if role != "seller" else "seller", params={})


def _extract_search(msg: str) -> tuple[str, float | None]:
    m = re.search(r"(?:under|below|less than|up to|max(?:imum)?|budget)\s*(?:pkr|rs\.?)?\s*([\d,]+)", msg)
    max_price = float(m.group(1).replace(",", "")) if m else None
    q = re.sub(r"(?:under|below|less than|up to|max(?:imum)?|budget)\s*(?:pkr|rs\.?)?\s*[\d,]+", " ", msg)
    q = re.sub(
        r"^\s*(find|search|looking for|show me|recommend|browse|compare|i want|i need|get me)\s*",
        "",
        q,
    )
    q = re.sub(r"\s+", " ", q).strip(" .,!?")
    return q, max_price


def _extract_listing_fields(msg: str) -> dict:
    price_m = (
        re.search(r"(?:pkr|rs\.?)\s*([\d,]+)", msg)
        or re.search(r"price\s*(?:of|is|at|=|:)?\s*([\d,]+)", msg)
        or re.search(r"\bat\s+([\d,]+)", msg)
    )
    stock_m = re.search(r"stock\s*(?:of|:|=)?\s*(\d+)", msg) or re.search(
        r"(\d+)\s*(?:pcs|pieces|units|in stock)", msg
    )
    title = msg
    title = re.sub(r"^\s*(please\s+)?(list|sell|create|add|publish)\s+(a|an|my|the)?\s*", "", title)
    title = re.sub(
        r"^\s*(i\s+want\s+to\s+(sell|list)|new\s+listing:?|create\s+a\s+listing\s+for|add\s+a\s+product:{0,1})\s*",
        "",
        title,
    )
    title = re.split(r"\s+(?:at|for|with\s+stock|stock|price|pkr|rs)\b", title, maxsplit=1)[0]
    title = title.strip(" .,!:-") or None
    return {
        "title": title,
        "price_pkr": float(price_m.group(1).replace(",", "")) if price_m else None,
        "stock": int(stock_m.group(1)) if stock_m else None,
        "description": "",
        "category": None,
    }
