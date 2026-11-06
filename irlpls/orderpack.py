"""Order pack: how a person finishes a commission plan, chosen only from what the provider's Capture Card documents.

irlpls plans and quotes. It never holds money, credentials or accounts, stays neutral, sends no email and places no
order. `how_to_pay(job, card)` returns one block that says which of five ways fits this provider and what the person
does, using only the card's own fields and URLs (never an invented one):

    x402              the card lists x402 and says an agent can pay: the user's own agent wallet settles it directly
    connect_once      self-serve account (OAuth MCP or key sign-up), then orders are approved in chat
    provider_checkout the provider's own hosted checkout, no API to connect
    request_quote     contract or quote-only provider: a drafted message the user sends from their own address
    by_hand           everything else (an approval step first, or nothing more documented)

Wallet signing stays off (adapters._a7.WALLET_SIGNING): the x402 kind describes the requirement; it signs nothing.
"""
import re

KINDS = ("x402", "connect_once", "provider_checkout", "request_quote", "by_hand")

_URL = re.compile(r"https?://[^\s,;)\]'\"<>]+")

DOES_NOT = ["charge you", "hold your money, login or account", "place the order for you"]


def _urls(text):
    return [u.rstrip(".:!?") for u in _URL.findall(text or "")]


def _first_url(*cands):
    for c in cands:
        if isinstance(c, str) and c.startswith(("https://", "http://")):
            return c
    return None


def _link(label, url):
    return [{"label": label, "url": url}] if url else []


def _span(minutes):
    if not minutes:
        return None
    if minutes < 120:
        return f"{int(minutes)} minutes"
    if minutes < 48 * 60:
        return f"{round(minutes / 60):g} hours"
    return f"{round(minutes / 1440):g} days"


def draft_message(job, card):
    """The request-for-quote the user sends from their own address: subject and body from the parsed request."""
    r, pol = job.request, getattr(job, "policy", None)
    need = (r.need or "").strip()
    where = r.place or ("anywhere" if getattr(r, "anywhere", False) else None)
    if where is None and r.lat is not None and r.lon is not None:
        where = f"{r.lat:g}, {r.lon:g}"
    deadline = _span(r.within_minutes)
    budget = r.max_price_usd if r.max_price_usd is not None else (pol.max_order_usd if pol and pol.max_order_usd is not None else None)
    proofs = ", ".join(f"{k} ({v})" for k, v in sorted((pol.require if pol else {}).items()))
    subject = "Quote request: " + (need[:70] + ("..." if len(need) > 70 else "")) + (f" ({where})" if where else "")
    lines = [f"Hello {card.get('name', 'team')},", "", "Please send a quote for new data captured to this specification.", "",
             f"What: {need}",
             f"Where: {where or 'please tell me what you can cover'}",
             f"Quantity: {r.quantity or 1}",
             f"Needed: {'within ' + deadline if deadline else 'please give your usual turnaround'}",
             "Rights: " + ("we need rights to use the delivered data to train AI models; please confirm this in writing."
                           if r.need_ai_training else
                           "please state the licence, including whether AI training and resale are allowed."),
             f"Budget: {'up to $' + format(budget, 'g') + ' USD' if budget is not None else 'please quote your price; no ceiling is set'}",
             "Proof wanted: " + (proofs + "." if proofs else "please say what proof comes with delivery (capture time, location, original files)."),
             "", "Please include price, what is included, delivery format and your terms.", "", "Thank you."]
    return {"subject": subject, "body": "\n".join(lines), "contact_url": card.get("provider_url")}


def _pack(kind, card, steps, needs, evidence, links, message=None):
    assert kind in KINDS and len(steps) <= 4 and all(len(s.split()) <= 14 for s in steps)
    out = {"kind": kind, "steps": steps, "needs_from_user": needs, "irlpls_does_not": list(DOES_NOT),
           "links": links, "evidence": evidence}
    if message:
        out["message"] = message
        out["irlpls_does_not"] = [d for d in out["irlpls_does_not"] if d != "place the order for you"] + ["send this for you", "place the order for you"]
    return out


def how_to_pay(job, card):
    """The `how_to_pay` block for a commission plan option, or None for a card with no commission route."""
    if "commission" not in ((card.get("delivery") or {}).get("modes") or []):
        return None
    name = card.get("name") or card.get("id")
    pay, api, acc = card.get("payment") or {}, card.get("api") or {}, card.get("access") or {}
    methods, rails = pay.get("methods") or [], pay.get("agent_rails") or []
    gate, cost = acc.get("gate"), acc.get("cost")
    mcp = api.get("mcp") or {}
    auth = [a for a in api.get("auth") or [] if a.get("type") != "none"]
    check = "Check delivery against the proof the card lists."

    if "x402" in methods and "x402" in rails and pay.get("agent_can_pay") and gate == "none":
        url = _first_url(api.get("docs_url"), api.get("base_url"), *_urls(pay.get("notes")))
        return _pack("x402", card,
                     ["Check the price and what is included in the plan.",
                      f"Ask {name} for its x402 payment requirement on this order.",
                      "Your own agent wallet settles it directly; set a spend cap first.",
                      check],
                     ["An agent wallet you control, funded with USDC", "Your yes on the exact price before it pays"],
                     ["payment.methods", "payment.agent_rails", "payment.agent_can_pay", "access.gate"] + (["api.docs_url"] if url == api.get("docs_url") else []),
                     _link(f"{name} API docs", url))

    if gate == "none" and api.get("available") and auth and (mcp.get("remote") or api.get("self_serve")):
        url = _first_url(mcp.get("remote"), *[u for a in auth for u in _urls(a.get("how_to_get"))], api.get("docs_url"))
        ev = ["access.gate", "api.auth"] + (["api.mcp.remote"] if url == mcp.get("remote") else
                                            ["api.docs_url"] if url == api.get("docs_url") else [])
        if api.get("self_serve"):
            ev.append("api.self_serve")
        return _pack("connect_once", card,
                     [f"Connect {name} once, through its own sign-in.",
                      "Add or fund a payment method in your account there.",
                      "Approve each order in chat; the provider charges you.",
                      check],
                     ["Sign in or create the account with the provider", "Add your own payment method there", "Your yes on each order"],
                     ev, _link(f"{name} " + ("sign-in" if url == mcp.get("remote") else "page"), url))

    if "hosted_checkout" in methods and gate == "none":
        url = _first_url(card.get("provider_url"))
        return _pack("provider_checkout", card,
                     [f"Open {name} on its own site.",
                      "Enter the same spec the plan shows.",
                      "Pay at the provider's own checkout.",
                      check],
                     ["Enter the order and pay on the provider's site"],
                     ["payment.methods", "access.gate", "provider_url"], _link(f"{name} website", url))

    if cost == "quote" or gate in ("contract", "enterprise"):
        url = _first_url(card.get("provider_url"))
        return _pack("request_quote", card,
                     ["Read the drafted message and fix any detail.",
                      "Send it from your own email or the provider's contact form.",
                      "Compare the quote with your budget and rights needs.",
                      "Sign and pay under the provider's own terms."],
                     ["Send the message yourself", "Agree the contract and payment with the provider"],
                     [f for f in ("access.cost", "access.gate", "payment.methods", "provider_url") if f != "payment.methods" or methods],
                     _link(f"{name} website", url), message=draft_message(job, card))

    url = _first_url(card.get("provider_url"), (api.get("docs_url")))
    return _pack("by_hand", card,
                 [f"Open {name} on its own site.",
                  "Request access if it asks for approval first." if gate == "approval" else "Create or sign in to your account there.",
                  "Place the order with the spec the plan shows.",
                  check],
                 ["Get access and place the order yourself, on the provider's site"],
                 [f for f in ("access.gate", "provider_url", "api.docs_url") if f != "api.docs_url" or url == api.get("docs_url")],
                 _link(f"{name} website", url))
