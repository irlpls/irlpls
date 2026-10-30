"""One-yes readiness: how close a provider is to "your person just says yes", derived from its card (never stored on it).

    ready_now      no account to make: the API needs no key, or an agent pays from the person's own wallet and can open
                   the account itself (x402 / MPP plus headless sign-up, where the terms let an agent act)
    one_consent    the person signs in once (OAuth an agent can register for itself: CIMD, or the deprecated dynamic
                   registration) or an agent creates the account by API, and the provider's terms let an agent act
    account_first  the person makes an account and copies a key (or nothing on the card says otherwise)
    contract       a sales or approval step comes before the first order, or the terms don't let an agent act

Only what a card states counts: an unstated field never moves a provider up. A broker uses this only to break a
near-tie (irlpls.broker.READY_MARGIN); it never outranks a much better fit.
"""

LEVELS = ("ready_now", "one_consent", "account_first", "contract")
WALLET_RAILS = ("x402", "mpp")

# The plain line a plan and a card page show. Short on purpose (card pages keep a word budget).
LINES = {
    ("ready_now", "keyless"): "No account or key needed.",
    ("ready_now", "wallet"): "No account: pays from your wallet.",
    ("ready_now", "wallet_signup"): "Your agent signs up and pays from your wallet.",
    ("one_consent", "oauth"): "One sign-in the first time.",
    ("one_consent", "headless_signup"): "One sign-up by your agent, the first time.",
    ("account_first", "manual"): "Needs an account first.",
    ("contract", "gate"): "Needs a sales or approval step first.",
    ("contract", "terms"): "Its terms bar agents without the provider's permission.",
}


def self_registers(m):
    """An OAuth method a client can register for itself: a Client ID Metadata Document (MCP 2026-07-28), or RFC 7591
    dynamic registration (deprecated by that spec, still accepted where a provider offers it)."""
    return isinstance(m, dict) and m.get("type") == "oauth2" and bool(m.get("cimd") or m.get("dynamic_registration"))


def agent_may_act(card):
    """True / False from the card's account.agent_may_act (a sourced statement of the provider's terms), else None."""
    v = ((card.get("account") or {}).get("agent_may_act"))
    return v.get("allowed") if isinstance(v, dict) and isinstance(v.get("allowed"), bool) else None


def readiness(card):
    """{"level", "via", "line"} for a card. See the module docstring for the rules."""
    acc, account = card.get("access") or {}, card.get("account") or {}
    rails = (card.get("payment") or {}).get("agent_rails") or []
    may = agent_may_act(card)
    auth = (card.get("api") or {}).get("auth") or []
    if acc.get("gate") in ("approval", "contract", "enterprise"):
        level, via = "contract", "gate"
    elif acc.get("automation_allowed") is False or may is False:
        level, via = "contract", "terms"
    elif acc.get("key_required") is False:
        level, via = "ready_now", "wallet" if any(r in WALLET_RAILS for r in rails) else "keyless"
    elif any(r in WALLET_RAILS for r in rails) and account.get("headless_signup") is True and may is True:
        level, via = "ready_now", "wallet_signup"
    elif may is True and any(self_registers(m) for m in auth):
        level, via = "one_consent", "oauth"
    elif may is True and account.get("headless_signup") is True:
        level, via = "one_consent", "headless_signup"
    else:
        level, via = "account_first", "manual"
    line = LINES[(level, via)]
    check = {"id": "an ID check", "business": "a business check"}.get(account.get("kyc"))
    if level == "one_consent" and check:  # still one approval, but say the extra first-time step plainly
        line = f"One {'sign-in' if via == 'oauth' else 'sign-up'} and {check} the first time."
    return {"level": level, "via": via, "line": line}


def rank(level):
    """Lower is closer to one yes."""
    return LEVELS.index(level) if level in LEVELS else len(LEVELS)


def counts(cards):
    out = {k: 0 for k in LEVELS}
    for c in cards:
        out[readiness(c)["level"]] += 1
    return out


# What this library needs can differ from the card's best path: RentAHuman's card offers wallet sign-up, but its adapter
# here calls the API with a key; Copernicus asks for an account to download, but its adapter only searches, keylessly.
# readiness_for() says what a person must do for THIS library, and keeps the card's own answer under `card`.
SDK_LINES = {
    ("one_consent", "sdk_signup"): "One sign-up by your agent for an API key ({env}).",
    ("account_first", "sdk_key"): "Needs an account and an API key ({env}) first.",
    ("ready_now", "sdk_keyless"): "No key needed for what this library reads.",
}


def readiness_for(card, sdk=None):
    """Readiness for what the reference library needs. sdk: {"env": key variable or None, "wallet": bool, "note": str?}
    for the provider's adapter, or None (no adapter: the card's readiness, unchanged). Never better than the card says
    where the card is stricter (a sales gate, terms that bar agents); never claims wallet-only when the adapter needs a key."""
    base = readiness(card)
    if not sdk:
        return base
    level, via = base["level"], base["via"]
    if level == "contract":
        return base
    acc, account = card.get("access") or {}, card.get("account") or {}
    if sdk.get("wallet"):
        return base
    env = sdk.get("env")
    if env:
        if level == "account_first":
            return base
        if via == "oauth" and any(w in env.upper() for w in ("TOKEN", "ACCESS")):
            return base   # the adapter takes the OAuth access token that the one sign-in gives
        if account.get("headless_signup") is True and agent_may_act(card) is True:
            new = ("one_consent", "sdk_signup")
        else:
            new = ("account_first", "sdk_key")
        if new[0] == level and via in ("headless_signup",):
            return base
        out = {"level": new[0], "via": new[1], "line": SDK_LINES[new].format(env=env), "card": base}
        return out
    # a keyless adapter where the card asks for an account: say exactly what needs none
    if level in ("account_first", "one_consent") and acc.get("key_required") is not False:
        line = sdk.get("note") or SDK_LINES[("ready_now", "sdk_keyless")]
        return {"level": "ready_now", "via": "sdk_keyless", "line": line, "card": base}
    return base
