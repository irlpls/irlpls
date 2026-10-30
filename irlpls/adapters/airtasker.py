"""Airtasker: post a local task (AU, GB, US, IE, NZ, SG) that Taskers make offers on, through Airtasker's official MCP
server with the person's own OAuth login. Token in AIRTASKER_ACCESS_TOKEN (from completing the OAuth flow below).

Sources (read 2026-10-28):
- MCP server repo: https://github.com/airtasker/airtasker-mcp-server (README: URL https://mcp.airtasker.com/mcp, OAuth
  first; tools browse_tasks, get_task, post_task, get_my_tasks, get_offers, accept_offer, get_profile; accept_offer
  "Hire a tasker and escrow payment", charges the connection fee for non-members).
- post_task arguments: the repo's skills/post-task/SKILL.md (title, description, price as a whole-dollar integer,
  location_type {type: in_person, latitude, longitude, display_name} or {type: remote}, date_type flexible | on_date |
  before_date, timeless_date when not flexible, specified_times optional; suburb centroid only, never an exact address).
- Protected-resource metadata: https://mcp.airtasker.com/.well-known/oauth-protected-resource (resource
  https://mcp.airtasker.com/, authorization server https://id.airtasker.com, scopes airtasker:read airtasker:write
  airtasker:payments, bearer in the header).
- Authorization-server metadata: https://id.airtasker.com/.well-known/oauth-authorization-server (authorize
  https://id.airtasker.com/authorize, token https://id.airtasker.com/oauth/token, dynamic registration
  https://id.airtasker.com/oidc/register, PKCE S256).

The authorization-code flow is never run here: the person signs in once in a browser (any MCP client such as Claude Code
does this: `claude mcp add --transport http airtasker https://mcp.airtasker.com/mcp`, then /mcp), and the access token is
set in AIRTASKER_ACCESS_TOKEN.

Unverified (no public tool schema; tools/list needs a signed-in session): the argument names of browse_tasks,
get_my_tasks and get_offers, the date format of timeless_date, and the shape of every tool's result. Those calls carry
that warning in their notes. Task statuses are not documented, so nothing is mapped (a placed task stays in_flight). This
adapter never calls accept_offer (the paid step): the person hires on Airtasker. There is no tool to read proof.
"""
from datetime import datetime, timedelta, timezone

from .. import http
from . import _mcp
from .base import Adapter

URL = "https://mcp.airtasker.com/mcp"
DOCS = "https://github.com/airtasker/airtasker-mcp-server"
AUTHORIZE = "https://id.airtasker.com/authorize"
OAUTH = {"grant": "authorization_code", "pkce": "S256", "sign_in": "the person signs in to Airtasker in a browser at the "
         "authorization URL and approves access once", "authorize_url": AUTHORIZE,
         "token_url": "https://id.airtasker.com/oauth/token", "registration_url": "https://id.airtasker.com/oidc/register",
         "resource": "https://mcp.airtasker.com/",
         "resource_metadata": "https://mcp.airtasker.com/.well-known/oauth-protected-resource",
         "scopes": ["airtasker:read", "airtasker:write"],
         "scopes_not_requested": {"airtasker:payments": "only accept_offer needs it; irlpls never hires or pays"}}
AUTH_NOTE = ("OAuth 2.0 authorization code + PKCE (S256), dynamic client registration at "
             "https://id.airtasker.com/oidc/register. Your person signs in at https://id.airtasker.com/authorize "
             "(scopes airtasker:read airtasker:write, resource https://mcp.airtasker.com/); exchange the code at "
             "https://id.airtasker.com/oauth/token. Any MCP client runs this for you.")
UNVERIFIED = "Argument names are not in Airtasker's public docs (unverified): list them with tools/list after signing in."


def _date(within_minutes, now=None):
    if not within_minutes:
        return None
    return ((now or datetime.now(timezone.utc)) + timedelta(minutes=within_minutes)).strftime("%Y-%m-%d")


def _with_oauth(pc):
    pc["auth"] = {**pc["auth"], "oauth": OAUTH}
    return pc


class Airtasker(Adapter):
    card_id = "airtasker"
    env = "AIRTASKER_ACCESS_TOKEN"
    how_to_get = ("Connect an MCP client to https://mcp.airtasker.com/mcp and sign in to Airtasker (OAuth, one consent); "
                  "set the access token it gets here.")
    can_commission = True

    # ---- ready calls (offline)
    def prepare(self, req, route, operation=None):
        """route "commission": post_task (default), or operation "read" (browse_tasks), "quote" (get_offers on a
        posted task), "status" (get_my_tasks). Nothing for "live": Airtasker has no live feed."""
        if route != "commission":
            return None
        op = operation or "order"
        kw = dict(key_name=self.env, get_key_url=AUTHORIZE, docs_url=DOCS, auth_kind="oauth2")
        if op == "order":
            args = self._post_args(self.spec_from_request(req), placeholders=True)
            ph = [{"name": n, "what": w, "where": "ask your person"} for n, w in
                  (("PRICE", "budget as a whole-dollar integer in the local currency"),
                   ("SUBURB", "suburb or neighbourhood name (never a street address)"),
                   ("LATITUDE", "suburb centroid latitude"), ("LONGITUDE", "suburb centroid longitude"))
                  if "{" + n + "}" in repr(args)]
            notes = ("post_task posts a task Taskers see and make offers on. Posting is free; nothing is charged until "
                     "accept_offer, which hires the Tasker and escrows payment (your person does that on Airtasker). "
                     "Confirm title, description, suburb, date and budget with your person first; never put contact "
                     "details or an exact address in it. Then poll get_offers. " + AUTH_NOTE)
            return _with_oauth(_mcp.prepared_tool_call(self.card_id, "order", URL, "post_task", args, notes=notes,
                                                       placeholders=ph or None, **kw))
        if op == "read":
            return _with_oauth(_mcp.prepared_tool_call(
                self.card_id, "read", URL, "browse_tasks", {}, safe=True, **kw,
                notes="Search the marketplace (open tasks near a place, typical prices). " + UNVERIFIED + " " + AUTH_NOTE))
        if op == "quote":
            return _with_oauth(_mcp.prepared_tool_call(
                self.card_id, "quote", URL, "get_offers", {"task_id": "{AIRTASKER_TASK_ID}"}, safe=True, **kw,
                placeholders=[{"name": "AIRTASKER_TASK_ID", "what": "the id of the task you posted",
                               "where": "post_task's result or get_my_tasks"}],
                notes="Offers on your posted task: each is a Tasker's price, a quote. Reading them charges nothing. " +
                      UNVERIFIED + " " + AUTH_NOTE))
        if op == "status":
            return _with_oauth(_mcp.prepared_tool_call(
                self.card_id, "status", URL, "get_my_tasks", {}, **kw,
                notes="Your tasks and their status. " + UNVERIFIED + " " + AUTH_NOTE))
        return None

    # ---- protocol-shim hook
    def spec_from_request(self, req, ext_id=None, deliver_by=None):
        need = req.get("need") or "Check this in person and send photos"
        cc = (req.get("country_code") or "").lower()
        price = req.get("max_price_usd") if cc == "us" else None   # whole dollars, only where the currency is USD
        due = (deliver_by or "")[:10] or _date(req.get("within_minutes"))
        s = {"title": need[:80], "description": need, "lat": req.get("lat"), "lon": req.get("lon"),
             "date": due, "price": int(price) if isinstance(price, (int, float)) and price >= 1 else None}
        return {k: v for k, v in s.items() if v is not None}

    @staticmethod
    def _post_args(spec, placeholders=False):
        def ph(v, name):
            return v if v not in (None, "") else ("{" + name + "}" if placeholders else None)
        if spec.get("remote"):
            loc = {"type": "remote"}
        else:
            lat, lon = spec.get("lat"), spec.get("lon")
            # a suburb centroid, never a street-level point: two decimals is about 1 km
            loc = {"type": "in_person", "latitude": round(lat, 2) if lat is not None else ph(None, "LATITUDE"),
                   "longitude": round(lon, 2) if lon is not None else ph(None, "LONGITUDE"),
                   "display_name": ph(spec.get("suburb"), "SUBURB")}
            loc = {k: v for k, v in loc.items() if v is not None}
        date = spec.get("date")
        a = {"title": (spec.get("title") or "")[:120] or None,
             "description": spec.get("description") or spec.get("notes") or spec.get("title"),
             "price": ph(spec.get("price"), "PRICE"), "location_type": loc,
             "date_type": spec.get("date_type") or ("before_date" if date else "flexible"),
             "timeless_date": date, "specified_times": spec.get("specified_times")}
        return {k: v for k, v in a.items() if v is not None}

    # ---- running
    def _call(self, tool, args):
        try:
            return _mcp.call_tool(URL, self.key(), tool, args, provider=self.card_id)
        except http.HTTPError as e:
            _mcp.raise_for_auth(self.card_id, e, "Sign in again through an MCP client and set a fresh AIRTASKER_ACCESS_TOKEN.")

    def commission(self, spec, confirm=False):
        """spec: title, description, price (whole dollars, local currency), suburb, lat, lon (suburb centroid) or
        remote=True, date (YYYY-MM-DD) with date_type on_date|before_date (default before_date, or flexible without a
        date), specified_times. Without confirm nothing is posted. confirm=True posts the task (free); this adapter
        never accepts an offer."""
        args = self._post_args(spec)
        loc = args["location_type"]
        missing = [m for m, ok in (("price: a whole-dollar budget", isinstance(args.get("price"), int) and args["price"] > 0),
                                   ("suburb: the suburb name, not a street address", loc["type"] == "remote" or "display_name" in loc),
                                   ("lat, lon: the suburb's centroid", loc["type"] == "remote" or "latitude" in loc and "longitude" in loc),
                                   ("date: needed when date_type is on_date or before_date",
                                    args["date_type"] == "flexible" or "timeless_date" in args))
                   if not ok]
        if not confirm:
            out = self._dry("POST", URL, _mcp.tools_call("post_task", args), {"Authorization": "Bearer <" + self.env + ">"})
            out["note"] = ("Nothing was posted. Pass confirm=True to post this task (free to post; Taskers will see it and "
                           "make offers; nothing is charged until your person accepts one on Airtasker).")
            if missing:
                out["missing"] = missing
            return out
        if missing:
            raise ValueError("airtasker: cannot post without " + "; ".join(missing))
        return self._call("post_task", args)

    def status(self, order):
        """get_my_tasks, narrowed to the posted task when its id can be found (result shape undocumented)."""
        res = self._call("get_my_tasks", {})
        tid = next((order.get(k) for k in ("id", "task_id", "taskId") if isinstance(order, dict) and order.get(k)), None)
        tasks = res.get("tasks") if isinstance(res, dict) else res if isinstance(res, list) else None
        if tid is not None and isinstance(tasks, list):
            hit = next((t for t in tasks if isinstance(t, dict) and str(t.get("id")) == str(tid)), None)
            if hit is not None:
                return hit
        return {"tasks": res}

    def offers(self, task_id):
        """get_offers on a posted task (read; argument name unverified, see the module docstring)."""
        return self._call("get_offers", {"task_id": task_id})
