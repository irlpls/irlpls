"""Quest (hireahuman.quest): post a short paid real-world task for local people in Singapore, Malaysia, the Philippines
and San Francisco, over Quest's own MCP server. Key in QUEST_API_KEY (sign in, then Profile > API keys).

Sources (read 2026-10-28):
- Agent docs: https://hireahuman.quest/agents/docs (MCP URL https://mcp.hireahuman.quest/mcp, Streamable HTTP,
  "authorization: Bearer YOUR_API_KEY", OAuth not supported yet; tools whoAmI, getQuestsV2, postQuest with every input
  and the postQuest statuses listed below).

What runs: getQuestsV2 (browse open quests and going rates; read, safe) and postQuest (order; real people see it and
travel, so only with confirm=True). There is no MCP tool yet for offers, hiring, payment, order status or proof ("A hire
tool is in development"), so this adapter has no status() and no deliverables: the person picks an offer and pays in the
Quest app or on hireahuman.quest. Prices are whole units of the poster's local currency (SGD, MYR, PHP or USD), so a
quote here is never a firm USD total.
"""
from datetime import datetime, timedelta, timezone

from .. import http
from . import _mcp
from .base import Adapter

URL = "https://mcp.hireahuman.quest/mcp"
DOCS = "https://hireahuman.quest/agents/docs"
KEYS_URL = "https://hireahuman.quest/profile/api-keys"

# startingCountry takes the full country name (docs: "Singapore, Malaysia, Philippines, United States, or online").
COUNTRY = {"sg": "Singapore", "my": "Malaysia", "ph": "Philippines", "us": "United States"}
CURRENCY = {"sg": "SGD", "my": "MYR", "ph": "PHP", "us": "USD"}

ORDER_NOTE = ("postQuest posts a paid task that real local people see and travel to do: confirm task, place, time and "
              "budget with your person first (Quest asks this). price is the budget in whole units of the account's local "
              "currency (S$30 = 30); the account and country come from the key. Nothing is paid by posting: your person "
              "picks an offer and pays in the Quest app or on hireahuman.quest. Returns {status, questID}; status pending "
              "means posted. Never put contact details in desc. Check going rates first with getQuestsV2.")


def _country(v):
    v = (v or "").strip()
    if v.lower() in COUNTRY:
        return COUNTRY[v.lower()]
    if v.upper().startswith("US-"):
        return "United States"
    return v or None


def _due(within_minutes, now=None):
    t = (now or datetime.now(timezone.utc)) + timedelta(minutes=within_minutes or 24 * 60)
    return t.strftime("%Y-%m-%dT00:00:00Z")


class Quest(Adapter):
    card_id = "quest"
    env = "QUEST_API_KEY"
    how_to_get = f"Sign in at hireahuman.quest, open {KEYS_URL}, click Generate new key and copy it (shown once)."
    can_commission = True

    # postQuest's returned status (documented list). "pending" = posted and in moderation, then shown to people.
    status_map = {"pending": "in_flight", "duplicate": "failed", "maximum price exceeded": "failed",
                  "illegal-content": "failed", "auto-delete": "failed", "admin-delete": "failed", "user-banned": "failed"}

    # ---- ready calls (offline)
    def prepare(self, req, route, operation=None):
        """route "commission": the postQuest order call (default) or, with operation="read", getQuestsV2 going rates
        near the place. Quest has no live feed and no status tool, so anything else is None."""
        if route != "commission":
            return None
        op = operation or "order"
        cc = (req.get("country_code") or "").lower()[:2]
        if op == "read":
            return self._prepare_read(req, cc)
        if op != "order":
            return None
        args = self._post_args(self.spec_from_request(req), placeholders=True)
        ph = [{"name": n, "what": w, "where": "ask your person"} for n, w in
              (("PRICE", f"budget in whole {CURRENCY.get(cc, 'local currency')} units"),
               ("ADDRESS", "street address or place where the task happens"),
               ("COUNTRY", "Singapore, Malaysia, Philippines or United States"))
              if "{" + n + "}" in repr(args)]
        return _mcp.prepared_tool_call(self.card_id, "order", URL, "postQuest", args, key_name=self.env,
                                       get_key_url=KEYS_URL, docs_url=DOCS, notes=ORDER_NOTE, placeholders=ph or None)

    def _prepare_read(self, req, cc):
        args = self._browse_args(cc, req.get("lat"), req.get("lon"))
        return _mcp.prepared_tool_call(self.card_id, "read", URL, "getQuestsV2", args, key_name=self.env,
                                       get_key_url=KEYS_URL, docs_url=DOCS, safe=True,
                                       notes="Browse open quests near the place to check going rates; posts nothing. Returns "
                                             "{quests, rowsSkipped, resultCount}; each price is in whole local currency units.")

    @staticmethod
    def _browse_args(cc, lat=None, lon=None, radius_m=5000):
        a = {"countryCode": cc.upper() if cc else None, "location": "in-person"}
        if lat is not None and lon is not None:
            a.update(lat=round(lat, 4), lng=round(lon, 4), radius=int(radius_m), sortOrder="nearby")
        return {k: v for k, v in a.items() if v is not None}

    # ---- protocol-shim hook
    def spec_from_request(self, req, ext_id=None, deliver_by=None):
        need = req.get("need") or "Check this in person and send a photo"
        cc = (req.get("country_code") or "").lower()
        price = req.get("max_price_usd") if cc.startswith("us") else None   # USD only where the account currency is USD
        s = {"title": need[:80], "description": need, "address": req.get("place"), "country": cc or None,
             "due_date": deliver_by or _due(req.get("within_minutes")), "people": req.get("quantity"),
             "price": int(price) if isinstance(price, (int, float)) and price > 0 else None}
        return {k: v for k, v in s.items() if v is not None}

    @staticmethod
    def _post_args(spec, placeholders=False):
        def ph(v, name):
            return v if v not in (None, "") else ("{" + name + "}" if placeholders else None)
        country = _country(spec.get("country"))
        online = country == "online"
        a = {"title": (spec.get("title") or "")[:120] or None,
             "desc": spec.get("description") or spec.get("notes") or spec.get("title"),
             "startingCountry": ph(country, "COUNTRY"),
             "dueDate": spec.get("due_date") or _due(None),
             "dueDateType": spec.get("due_date_type", "before"),
             "timeRange": spec.get("time_range", "any time"),
             "numberOfHours": spec.get("hours", 0.5),
             "numOffers": max(1, min(int(spec.get("people") or 1), 20)),
             "paymentRecurrence": "per hour" if spec.get("per_hour") else "one time payment",
             "price": ph(spec.get("price"), "PRICE"),
             "scopeOfReach": spec.get("scope", "local"),
             "startingAddress": None if online else ph(spec.get("address"), "ADDRESS"),
             "requirements": spec.get("requirements"), "screeningQuestions": spec.get("screening_questions"),
             "dateRangeEnd": spec.get("date_range_end"), "urgent": spec.get("urgent")}
        return {k: v for k, v in a.items() if v is not None}

    # ---- running
    def _call(self, tool, args):
        try:
            return _mcp.call_tool(URL, self.key(), tool, args, provider=self.card_id)
        except http.HTTPError as e:
            _mcp.raise_for_auth(self.card_id, e, f"Create a new key at {KEYS_URL}.")

    def going_rates(self, country_code, lat=None, lon=None, radius_m=5000):
        """getQuestsV2 near a point: open quests and their prices (whole local currency units). Read only."""
        res = self._call("getQuestsV2", self._browse_args((country_code or "").lower(), lat, lon, radius_m))
        quests = res.get("quests") if isinstance(res, dict) else None
        prices = sorted(q["price"] for q in quests or [] if isinstance(q, dict) and isinstance(q.get("price"), (int, float))
                        and not isinstance(q.get("price"), bool))
        return {"currency": CURRENCY.get((country_code or "").lower()[:2]), "count": len(prices),
                "median": prices[len(prices) // 2] if prices else None, "min": prices[0] if prices else None,
                "max": prices[-1] if prices else None, "quests": quests or []}

    def commission(self, spec, confirm=False):
        """spec: title, description, address, country (SG/MY/PH/US or the name), due_date (ISO), due_date_type
        (exact|before|range), time_range, hours, people (1-20), price (whole local units), per_hour, scope
        (local|global), requirements. Without confirm nothing is posted; with the key set, the dry run adds the going
        rates nearby (indicative only, local currency: never a firm USD quote)."""
        args = self._post_args(spec)
        missing = [m for m, ok in (("price: the budget in whole local currency units", "price" in args),
                                   ("address: where the task happens", "startingAddress" in args or args.get("startingCountry") == "online"),
                                   ("country: Singapore, Malaysia, Philippines or United States", "startingCountry" in args))
                   if not ok]
        if not confirm:
            out = self._dry("POST", URL, _mcp.tools_call("postQuest", args), {"Authorization": "Bearer <" + self.env + ">"})
            out["note"] = "Nothing was posted. Pass confirm=True to post this quest; real people will see it and may travel."
            if missing:
                out["missing"] = missing
            if self.configured() and spec.get("quote", True) and spec.get("country"):
                cc = str(spec["country"]).lower()[:2]
                cc = next((k for k, v in COUNTRY.items() if v.lower() == str(spec["country"]).lower()), cc)
                out["going_rates"] = {k: v for k, v in self.going_rates(cc, spec.get("lat"), spec.get("lon")).items() if k != "quests"}
            return out
        if missing:
            raise ValueError("quest: cannot post without " + "; ".join(missing))
        return self._call("postQuest", args)
