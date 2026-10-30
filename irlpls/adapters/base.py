"""Adapter interface. An adapter turns a place (and a request) into a provider API call.

live():       pull what is reporting now (sensor feeds). Safe; costs at most API quota.
commission(): ask the provider to capture something you specify (satellite tasking, a street video, a study).
              Can cost money, so it is a dry run unless confirm=True, and returns exactly what it would send.
IRLpls does not search archives of old data.
"""
import os
from datetime import datetime, timezone


class MissingCredentials(RuntimeError):
    pass


class NotSupported(NotImplementedError):
    pass


class MissingDetails(NotSupported, ValueError):
    """An order is missing details the person must give (an address, a price, what to capture): nothing is sent.
    `missing` lists them ("field: what to give")."""
    def __init__(self, card_id, missing):
        self.card_id, self.missing = card_id, list(missing)
        super().__init__(f"{card_id}: not sent, missing " + "; ".join(self.missing))


def observation(provider, kind, phenomenon_time, lat, lon, value=None, unit=None, label=None, url=None, **extra):
    """One result, in SOSA/SensorThings terms: phenomenon_time is when the observed thing happened (required, even if
    unknown); result_time is when the provider produced the result, if it says; retrieved_at is when irlpls got it.
    What irlpls hands out (live(), a Job's results) passes through normalise(): UTC times with Z, one unit vocabulary."""
    o = {"provider": provider, "kind": kind, "phenomenon_time": phenomenon_time, "result_time": extra.pop("result_time", None),
         "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "lat": lat, "lon": lon,
         "value": value, "unit": unit, "label": label, "url": url}
    o.update(extra)
    return {k: v for k, v in o.items() if v is not None or k == "phenomenon_time"}


# One unit vocabulary for every result (STANDARD 6 results[].unit; schema/job.schema.json). Providers' spellings map
# onto it; a datum or reference ("above MLLW", stage datum) moves to `datum` so the unit stays a unit.
UNITS = {
    "°C": ("degC", "deg C", "Cel", "celsius", "Celsius", "°c", "℃", "wmoUnit:degC"),
    "°F": ("degF", "deg F", "[degF]", "fahrenheit", "Fahrenheit", "°f", "℉"),
    "ft³/s": ("ft^3/s", "ft3/s", "cfs", "CFS", "ft3 s-1", "ft³ s-1", "cubic feet per second"),
    "m³/s": ("m3/s", "m^3/s", "m3 s-1", "m³ s-1", "m3s-1", "cumecs", "cubic metres per second", "cubic meters per second"),
    "µg/m³": ("ug/m3", "ug/m^3", "µg/m3", "μg/m³", "μg/m3", "ug m-3", "µg m-3", "microgram/m3"),
    "km/h": ("km_h-1", "km/hr", "kmh", "kph", "km h-1", "wmoUnit:km_h-1"),
    "m/s": ("m_s-1", "m s-1", "mps", "m/sec", "wmoUnit:m_s-1"),
    "kn": ("kts", "kt", "knot", "knots"),
    "°": ("degT", "deg", "degree", "degrees", "deg_true", "degree_(angle)", "wmoUnit:degree_(angle)"),
    "hPa": ("mbar", "millibar", "hectopascal"),
    "%": ("percent", "%RH", "percent_relative_humidity", "wmoUnit:percent"),
    "m": ("meters", "metres", "meter", "metre", "wmoUnit:m"),
    "ft": ("feet", "foot"),
    "cm": ("centimetres", "centimeters"),
    "mm": ("millimetres", "millimeters"),
}
_UNIT = {alias: u for u, aliases in UNITS.items() for alias in aliases}
_DATUM_UNITS = {"mASD": ("m", "stage datum"), "mAOD": ("m", "Ordnance Datum (AOD)"), "mAODN": ("m", "Ordnance Datum Newlyn"),
                "mNN": ("m", "NN"), "mNHN": ("m", "NHN"), "cmPNP": ("cm", "gauge zero (PNP)")}
_MAGNITUDE = ("ml", "mb", "mw", "md", "ms", "mwr", "mww", "mwc", "mwb", "mb_lg", "mlv", "mlg", "mlr", "mh", "mi", "mpv",
              "magnitude", "mag", "m")
QUAKE_PROVIDERS = ("usgs-earthquakes", "emsc-seismicportal", "geonet-nz")


def dedupe(rows):
    """Drop rows that repeat another's reading: the same provider, label, value, unit and time within about 1 km (one
    gauge listed under two station codes, as Hub'Eau does). The first is kept; rows without a value are all kept."""
    seen, out = set(), []
    for o in rows:
        if not isinstance(o, dict) or o.get("value") is None:
            out.append(o)
            continue
        r = lambda x: round(x, 2) if isinstance(x, (int, float)) else None
        k = (o.get("provider"), str(o.get("label") or "").lower(), str(o.get("value")), o.get("unit"), o.get("phenomenon_time"),
             r(o.get("lat")), r(o.get("lon")))
        if k in seen:
            continue
        seen.add(k)
        out.append(o)
    return out


def unit_of(unit, provider=None):
    """(unit, datum, magnitude_type) in irlpls's vocabulary for a provider's unit string."""
    if not isinstance(unit, str) or not unit.strip():
        return unit, None, None
    u = unit.strip()
    if provider in QUAKE_PROVIDERS and u.lower() in _MAGNITUDE:
        return "M", None, (u.lower() if u.lower() not in ("magnitude", "mag", "m") else None)
    if u in _DATUM_UNITS:
        return (*_DATUM_UNITS[u], None)
    m = _re.match(r"^(m|ft|cm|mm)\s+(?:above|over|relative to)\s+(.+)$", u)
    if m:
        return m.group(1), m.group(2).strip(), None
    return _UNIT.get(u, u), None, None


def utc_z(t):
    """An ISO 8601 time with any offset as UTC with Z ("2026-10-29T07:00:00+01:00" -> "2026-10-29T06:00:00Z"); a time
    without an offset, or anything unreadable, is returned unchanged (never guessed)."""
    if not isinstance(t, str) or not t or t.endswith("Z"):
        return t
    try:
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return t
    if d.tzinfo is None:
        return t
    d = d.astimezone(timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" if d.microsecond else d.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalise(o):
    """A result in one format: phenomenon_time and result_time in UTC with Z, the unit in irlpls's vocabulary (a datum
    to `datum`, an earthquake magnitude type to `magnitude_type`), age_s when the time is known. Idempotent."""
    if not isinstance(o, dict):
        return o
    for k in ("phenomenon_time", "result_time"):
        if isinstance(o.get(k), str):
            o[k] = utc_z(o[k])
    if isinstance(o.get("unit"), str):
        u, datum, mt = unit_of(o["unit"], o.get("provider"))
        o["unit"] = u
        if datum and not o.get("datum"):
            o["datum"] = datum
        if mt and not o.get("magnitude_type"):
            o["magnitude_type"] = mt
    if isinstance(o.get("daily_stats"), dict) and isinstance(o["daily_stats"].get("unit"), str):
        o["daily_stats"] = dict(o["daily_stats"], unit=unit_of(o["daily_stats"]["unit"], o.get("provider"))[0])
    if o.get("age_s") is None and isinstance(o.get("phenomenon_time"), str):
        a = age_s(o["phenomenon_time"])
        if a is not None:
            o["age_s"] = a
    return o


def age_s(phenomenon_time, now=None):
    """Seconds since an ISO 8601 timestamp (any offset, or Z), or None if it can't be read."""
    try:
        t = datetime.fromisoformat(str(phenomenon_time).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        return None
    return int(((now or datetime.now(timezone.utc)) - t).total_seconds())


def warning(provider, code, msg, **extra):
    """Something the adapter tried and couldn't do (a rate limit, a fallback). Reported, never hidden."""
    return {"provider": provider, "code": code, "msg": msg, **extra}


AUTH_KINDS = ("none", "api_key_header", "api_key_query", "bearer", "basic", "oauth2", "wallet", "other")
OPERATIONS = ("read", "quote", "order", "status")


def prepared_call(card_id, operation, method, url, *, headers=None, body=None, auth_kind="none", key_name=None,
                  key_in=None, get_key_url=None, docs_url=None, notes=None, safe_to_run_without_approval=None,
                  placeholders=None):
    """A ready call to a provider's own API, filled in from an ask: what an agent (or the SDK, once the user gives the key)
    sends. Credentials are never filled in, only named: a header or query value is written as the placeholder
    "{KEY_NAME}" and auth says which key it is and where to get it. Built offline; nothing is sent.

    safe_to_run_without_approval defaults to True only for read/status calls; quote and order calls default to False
    (a quote may need an account; an order may cost money or dispatch a person).
    placeholders: other account values the call needs, written "{NAME}" in it, as [{"name", "what", "where"}] (a
    contract id, a project id, a package id)."""
    if operation not in OPERATIONS:
        raise ValueError(f"operation must be one of {OPERATIONS}")
    if auth_kind not in AUTH_KINDS:
        raise ValueError(f"auth kind must be one of {AUTH_KINDS}")
    if safe_to_run_without_approval is None:
        safe_to_run_without_approval = operation in ("read", "status")
    auth = {"kind": auth_kind}
    if auth_kind != "none":
        auth.update({"key_name": key_name, "placeholder": "{" + key_name + "}" if key_name else None, "in": key_in,
                     "get_key_url": get_key_url})
    c = {"card_id": card_id, "operation": operation, "method": method, "url": url, "headers": dict(headers or {}),
         "body": body, "auth": {k: v for k, v in auth.items() if v is not None}, "placeholders": list(placeholders) if placeholders else None,
         "docs_url": docs_url, "notes": notes, "safe_to_run_without_approval": bool(safe_to_run_without_approval)}
    return {k: v for k, v in c.items() if v is not None}


def pick_operation(route, operation, want_route, supported, default):
    """The operation a prepare() should build, or None: the route must be `want_route`, and the operation (or `default`
    when None) one of `supported`."""
    if route != want_route:
        return None
    op = operation or default
    return op if op in supported else None


def query(params):
    """A query string (keeps , : and {PLACEHOLDER} braces readable), for ready-call URLs."""
    import urllib.parse
    return urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, safe=",:{}")


import re as _re

_STREET = _re.compile(r"\b\d{1,6}[A-Za-z]?(?:-\d{1,5})?\s+(?:[NSEW]\.?\s+)?[A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,4}?\s+"
                      r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Place|Hwy|Highway|"
                      r"Pkwy|Parkway|Ter|Terrace|Cir|Circle|Sq|Square|Trl|Trail|Row|Alley|Broadway)\b\.?", _re.I)


def street_address(place):
    """The street address inside a place ("Joe's Diner, 50 Elm St, Burlington, VT" -> "50 Elm St, Burlington, VT"), or
    None when the place is not one (a city, a landmark). A provider that dispatches to an address gets only this."""
    if not isinstance(place, str):
        return None
    m = _STREET.search(place)
    return place[m.start():].strip() if m else None


def spec_from_request(adapter, req, ext_id=None, deliver_by=None):
    """The one order spec for an ask: the adapter's own spec_from_request hook when it has one, else the generic fields
    (address, lat, lon, notes, quantity, deadline). The broker's submit(), the ready call (prepare) and the protocol shim
    all start from this, so a dry run, a ready call and a placed order carry the same details. `req` is the broker's
    request dict; its "deliver_by" (ISO 8601 UTC, fixed when the job is made) is the deadline unless one is passed."""
    deliver_by = deliver_by or req.get("deliver_by")
    hook = getattr(adapter, "spec_from_request", None)
    if callable(hook):
        s = hook(req, ext_id, deliver_by)
    else:
        need = req.get("need")
        s = {"address": street_address(req.get("place")), "place": req.get("place"), "lat": req.get("lat"),
             "lon": req.get("lon"), "notes": need, "description": need, "quantity": req.get("quantity"),
             "deliver_by": deliver_by, "external_id": ext_id}
    return {k: v for k, v in (s or {}).items() if v is not None}


SECRET_HEADER_WORDS =("key", "auth", "token", "secret", "client", "password", "session", "cookie")


def mask_headers(headers):
    """Headers safe to show: any whose name contains key, auth, token, secret, client, password, session or cookie
    (any case) is written "***"."""
    return {k: ("***" if any(w in str(k).lower() for w in SECRET_HEADER_WORDS) else v) for k, v in (headers or {}).items()}


QUOTE_AMOUNT_KEYS = ("totalPrice", "total_price", "price", "orderCost", "cost", "total", "provider_total", "amount")


def _num(v):
    from decimal import Decimal
    import math
    if type(v) in (int, float) and math.isfinite(v) and v >= 0:
        return Decimal(repr(v)) if isinstance(v, float) else Decimal(v)
    return None


def quote_price(dry):
    """The provider's price in a dry run, as (amount Decimal or None, currency or None, firm).

    Read from dry["quote"] (the first of QUOTE_AMOUNT_KEYS holding a real number; its "currency", USD when unstated) or
    else dry["provider_quote"] {amount, currency}. Firm only when the currency is USD and the quote does not say
    firm: false. Any other currency (EUR, KRW, GBP, credits, USDC) stays indicative: the amount is shown with its own
    currency and never converted."""
    if not isinstance(dry, dict):
        return None, None, False
    for src in ("quote", "provider_quote"):
        q = dry.get(src)
        if not isinstance(q, dict):
            continue
        amt = next((a for a in (_num(q.get(k)) for k in QUOTE_AMOUNT_KEYS) if a is not None), None)
        if amt is None:
            continue
        cur = q.get("currency")
        cur = str(cur).strip().upper() if isinstance(cur, str) and cur.strip() else ("USD" if src == "quote" else None)
        firm = src == "quote" and cur == "USD" and q.get("firm") is not False
        return amt, cur, firm
    return None, None, False


ZERO_DECIMAL = ("BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF")


def minor_to_major(amount_minor, currency):
    """An integer amount in a currency's minor units (cents, pence) as a number in its major units, or None. A unit
    change (ISO 4217 exponent 2, or 0 for KRW, JPY ...), never a currency conversion."""
    if type(amount_minor) is not int or amount_minor < 0 or not isinstance(currency, str):
        return None
    from decimal import Decimal
    return float(Decimal(amount_minor) / (1 if currency.upper() in ZERO_DECIMAL else 100))


def usd_total(dry):
    """A dry run's firm USD total (Decimal), or None: what a USD ceiling may be checked against."""
    amt, _, firm = quote_price(dry)
    return amt if firm else None


class Results(list):
    """Observations, plus the warnings met while getting them (job.warnings in the broker)."""
    def __init__(self, items=(), warnings=()):
        super().__init__(items)
        self.warnings = list(warnings)


class Adapter:
    card_id = None
    env = None           # name of the environment variable holding the key, if any
    how_to_get = ""      # where a developer gets that key
    can_live = False
    can_commission = False
    readiness_note = None  # what a keyless adapter reads without the account its card asks for (readiness.readiness_for)

    def configured(self):
        return not self.env or bool(os.environ.get(self.env))

    def key(self, required=True):
        if not self.env:
            return None
        if not required:
            return os.environ.get(self.env) or "<" + self.env + ">"
        v = os.environ.get(self.env)
        if not v:
            raise MissingCredentials(f"{self.card_id}: set {self.env}. {self.how_to_get}".strip())
        return v

    def live(self, lat, lon, radius_km=10, limit=5, **kw):
        raise NotSupported(f"{self.card_id} has no live feed in this SDK")

    def commission(self, spec, confirm=False):
        raise NotSupported(f"{self.card_id} has no commission() in this SDK yet")

    def status(self, order):
        """Provider-side status of a placed order (the dict commission(confirm=True) returned)."""
        raise NotSupported(f"{self.card_id} has no status() in this SDK yet")

    can_cancel = False

    def cancel(self, order):
        """Ask the provider to cancel a placed order, through a cancel call its docs list. Only then set can_cancel."""
        raise NotSupported(f"{self.card_id} has no cancel() in this SDK yet")

    # Protocol-shim hooks (irlpls/protocol/shim.py), so a provider's quirks live in its own module, not the shim.
    # spec_from_request(req, ext_id, deliver_by) -> the `spec` dict commission() takes, built from a protocol request
    #   ({need, place, lat, lon, quantity, ...}); ext_id is the shim's idempotency id, pass it to the provider if it has one.
    # order_total(resp) -> Decimal USD the provider says it will charge, read from commission(confirm=True)'s response
    #   (documented fields only), or None.
    spec_from_request = None
    order_total = None

    def prepare(self, req, route, operation=None):
        """The ready call (prepared_call(...)) for this ask, or None if this adapter can't build one. `req` is the
        broker's request dict (need, place, lat, lon, quantity, within_minutes...), `route` "live" or "commission",
        `operation` one of OPERATIONS (None: the adapter's default for the route). The broker asks for every operation
        and lists those that come back as `prepared_calls` {operation: call}. Offline: no network, and never a real
        credential in the output."""
        return None

    # Wallet-signed providers (no API key: every paid call is signed by the person's own wallet). The catalog lists them
    # as `sdk-callable-wallet`, never as "no key needed"; the broker treats them as needing credentials.
    needs_wallet = False

    # Two-step orders. Some providers hold a placed order for an explicit, paid second step (Capella's cost estimate,
    # GLOBHE's quotes). pending_approvals(status_resp) lists what is waiting for the person:
    #   [{"id": approval id or None, "amount": Decimal or None, "currency": "USD"|..., "label": str, ...}]
    # approve(order, approval_id=None, confirm=False) sends the approval: a dry run unless confirm=True. The broker,
    # shim and MCP server only ever call it with confirm=True after a person's explicit, per-order approval, never
    # under a standing mandate.
    def pending_approvals(self, resp):
        return []

    def approve(self, order, approval_id=None, confirm=False):
        raise NotSupported(f"{self.card_id} has no second approval step")

    # Status mapping. Only statuses the provider's own docs list are mapped; an unknown or unmapped status keeps the
    # job in_flight and is reported as the provider sent it. Nothing here invents a status.
    status_path = ("status",)   # where the provider's status string sits in the status() response
    status_map = {}             # provider status (case-insensitive) -> "in_flight" | "delivered" | "failed"

    def provider_status(self, resp):
        """The provider's own status string from a status() response, or None if it has none."""
        v = resp
        for k in self.status_path:
            v = v.get(k) if isinstance(v, dict) else None
        return v if isinstance(v, str) else None

    def map_status(self, raw):
        """irlpls job state for a provider status: in_flight, delivered or failed; None if this adapter can't tell."""
        return self.status_map.get(raw.lower()) if isinstance(raw, str) else None

    def deliverables(self, resp):
        """Results (observation dicts) in a status() response for a delivered order. Empty if the response has none."""
        return []

    def _dry(self, method, url, body=None, headers=None):
        safe = mask_headers(headers)
        return {"dry_run": True, "would_send": {"method": method, "url": url, "headers": safe, "body": body},
                "note": "Nothing was sent. Pass confirm=True to place this order; it may cost money."}
