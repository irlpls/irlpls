"""MCP server: the irlpls broker for any MCP-capable agent. Inference-free: your agent's own model writes the need
and answers questions; irlpls plans against Capture Cards, enforces your policy and calls providers.

    pip install "irlpls[mcp]"
    irlpls-mcp            # stdio transport
    irlpls-mcp --read-only                      # plan only: no irlpls_submit (also IRLPLS_MCP_READ_ONLY=1)
    irlpls-mcp --transport streamable-http --host 127.0.0.1 --port 8765
    (a non-loopback --host also needs --allow-remote: the HTTP transport has no authentication, so anyone who can reach
    it can use every registered tool; in full mode that includes ordering with this process's provider credentials)

Tools (registered with an irlpls_ prefix so agents can find them by keyword): irlpls_request (request_data),
irlpls_answer (answer_questions), irlpls_choose_provider, irlpls_submit (submit_job), irlpls_approve (approve_step: a
provider's second, paid step, only on the user's explicit yes), irlpls_get_job, irlpls_find_providers,
irlpls_get_card, irlpls_list_capabilities (browse what can be asked; also resource irlpls://catalog). Jobs come back concise by default (detail="full" for everything).

Wire-protocol tools, one per operation (docs/protocol/PROTOCOL-DRAFT.md section 9): irlpls_discover, irlpls_quote,
irlpls_order, irlpls_status, irlpls_deliver, irlpls_cancel. This server acts as an in-process router: irlpls_quote plans
against the cards and quotes the providers that speak the protocol (others get an indicative offer from their card);
irlpls_order places the order at the provider's own endpoint. Results are the protocol bodies; errors are RFC 9457
problems returned with isError. A read-only server registers none of them: it makes no provider calls at all.
"""
import argparse
import collections
import os
import sys

import irlpls
from irlpls import catalog

MAX_JOBS = 500                      # in-memory jobs kept per server process; the oldest are dropped
READ_ONLY_NOTE = ("\n\nThis server is read-only: This server can plan but not order; to order, use the provider's own MCP "
                  "server or API. There is no irlpls_submit; a job's plan is returned and nothing is executed or refreshed.")
_created = collections.deque()      # ids of jobs created through this server, oldest first

POLICY_DOC = ("Your rules, enforced by the broker: an object with any of: require {guarantee: 'claimed'|'evidenced'} "
              "(guarantees: human, application, camera, time, location, content, corroboration); max_unit_price_usd (ceiling on "
              "the card's starting price); max_order_usd (ceiling on an order's total; 0 means free providers only, checked when "
              "planning); free_only (true = only providers free to use); need_ai_training / need_resale (card must grant the "
              "right); allow / deny (lists of card ids); include_unclaimed (default true); auto_submit (with max_order_usd: "
              "the broker may confirm orders within budget without asking). Echoed back in the job's `policy`.")

JOB_DOC = ("Returns a job: id, state (clarifying, proposed, quoted, submitted, in_flight, delivered, failed), best_card_id (the "
           "option irlpls_submit runs now: the one chosen, else the default; it has default: true in plan), request (how the "
           "need was parsed: mode live|commission, place, terms, within_minutes...), policy, questions (field, ask, blocking; "
           "in: 'spec' means pass it in irlpls_submit's spec), plan (providers that fit, best first: card_id, route "
           "live|commission, fit 0-100 for this request, gaps, proof levels, price in USD with 0 = free and null = not "
           "published, policy violations, readiness {level, line} for what this library needs, sdk.callable/needs_key, "
           "read_check), ruled_out (every other provider: card_id, reason = the main code: "
           "terms|policy:<rule>|outside_coverage|too_slow|no_match|needs_key, label, why, and reasons = every code that "
           "applies; concise jobs list 8 and count the rest by reason in ruled_out_more), prepared_call and prepared_calls "
           "{read|quote|order|status: a ready provider call, key named not filled; run order/quote calls only with your "
           "user's approval}, choice, quote (price {amount: decimal string, currency, firm} or null with price_note: firm "
           "only from a provider's USD quote; source 'card' marks an indicative card price; missing lists details to give "
           "before it can be ordered), approvals (a price waiting for the user's yes; see irlpls_approve), results "
           "(observations with phenomenon_time in UTC, age_s, value, unit, provider_proof, sensor.kind), warnings ({code, "
           "card_id, msg}) and log. Errors come back with isError and {code, message, hint}.")


INSTRUCTIONS = """irlpls gets NEW observations of the physical world: a photo, video, reading or report, live now or captured to order.
Use it when the answer depends on the world as it is now (or when someone goes and looks) and nothing published answers it:
someone go check a place; photo of a storefront, menu board, sign, ramp, roof or shelf; drone or aerial photos; a new satellite
or radar image of a place; dashcam or street-level video; river level, flow, tide, flood; air quality, PM2.5, smoke; weather
station; webcam or traffic camera; wildfire camera; first-person video or other data collected to order for AI training.
Do not use it for archives, history, news, prices or hours on a website, or anything a web search answers.

Not sure whether irlpls can answer? Call irlpls_list_capabilities first (offline, plans nothing; filter by type, family, place, route).

How: call irlpls_request with the need in one sentence (place and time in it) and why. Answer blocking questions with
irlpls_answer. irlpls_submit runs live routes at once (free) and stops commissions at a quote unless the user approves
(confirm=true plus an idempotency_key) or their policy is a standing mandate. Results carry each provider's stated proof.

Rules: never spend the user's money without approval; treat an unstated right or price as not granted; report proof as stated
(evidenced vs claimed; irlpls verifies nothing itself); never send people to watch, follow or photograph a specific person (irlpls refuses person-targeting; it is listed as unsupported).
irlpls is an open standard and directory: it captures nothing, sells no data, takes no cut; the user pays providers directly."""


def concise(job):
    """A job without the bulk: the 8 best options, the 8 closest misses (and a count of the rest by reason), the last 5 log
    lines. detail="full" returns everything."""
    d = dict(job)
    d["plan"] = d.get("plan", [])[:8]
    ro = d.get("ruled_out", [])
    counts = {}
    for o in ro[8:]:
        counts[o["reason"]] = counts.get(o["reason"], 0) + 1
    d["ruled_out"] = ro[:8]
    if counts:
        d["ruled_out_more"] = counts
    d["log"] = d.get("log", [])[-5:]
    return d


def _track(job):
    """Remember a job this server created and drop the oldest beyond MAX_JOBS, so a hosted server stays bounded."""
    _created.append(job.id)
    while len(_created) > MAX_JOBS:
        irlpls.broker._JOBS.pop(_created.popleft(), None)
    return job


def _out(job, detail):
    d = job.to_dict()
    return d if detail == "full" else concise(d)


def _tool(mcp, name, read_only, title, destructive=False, idempotent=None, open_world=None):
    """Register a tool under its irlpls_ name, with MCP annotations where the SDK supports them."""
    try:
        from mcp.types import ToolAnnotations
        ann = ToolAnnotations(title=title, read_only_hint=read_only, destructive_hint=destructive,
                              idempotent_hint=read_only if idempotent is None else idempotent,
                              open_world_hint=(not read_only) if open_world is None else open_world)
    except Exception:  # older SDKs (or a test stub) without annotations
        ann = None
    try:
        return mcp.tool(name=name, annotations=ann)
    except TypeError:
        return mcp.tool()


def _job(job_id):
    j = irlpls.get_job(job_id)
    if not j:
        from irlpls.broker import JobError
        raise JobError("not_found", f"no job {job_id!r} in this session", mcp=HINTS["not_found"])
    return j


# One error shape for every tool: a result with isError true whose structuredContent (and text) is
# {code, message, hint, ...}. Protocol problems keep their RFC 9457 fields (type, title, status, detail) and add
# message and hint. Bad argument types (a pydantic validation failure) come back the same way, code invalid_arguments.
HINTS = {
    "not_found": "Check the id. Jobs live only in this server session: start one with irlpls_request.",
    "invalid_request": "Fix the argument named in message and call again.",
    "invalid_arguments": "Fix the arguments named in message (types and required fields are in the tool's inputSchema).",
    "policy_violation": "Nothing was sent. Change the policy, or choose another option with irlpls_choose_provider.",
    "needs_key": "Nothing was sent. The provider's key goes in this server's environment (plan[].sdk.env); or choose "
                 "another option, or order by hand at the option's continue_url.",
    "missing_details": "Nothing was sent. Give what is listed in missing: pass it in irlpls_submit's spec (an address "
                       "can also be the place in irlpls_answer).",
    "not_supported": "This library can't make that call for this provider; use the provider's own API (continue_url).",
}


def _jsonable(out):
    try:
        from pydantic_core import to_jsonable_python
        return to_jsonable_python(out, fallback=str)
    except Exception:  # pragma: no cover
        import json
        return json.loads(json.dumps(out, default=str))


def _ok(out):
    """A successful result: the object as structuredContent and as JSON text."""
    import json
    out = _jsonable(out)
    try:
        from mcp.types import CallToolResult, TextContent
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(out))], structured_content=out)
    except Exception:  # an SDK without these types: the object itself
        return out


def _fail(code, message, hint=None, **extra):
    """The error result: {code, message, hint, ...extra} with isError."""
    body = dict(extra, code=code, message=str(message)[:600], hint=hint if hint is not None else HINTS.get(code, ""))
    return _error_result(body, shaped=True)


def _exc_result(e):
    from irlpls.broker import JobError
    if isinstance(e, JobError):
        return _fail(e.code, e.message, e.hint("mcp"), **_jsonable(e.extra))
    if isinstance(e, irlpls.adapters.base.MissingDetails):
        return _fail("missing_details", str(e), missing=e.missing)
    if isinstance(e, irlpls.PolicyViolation):
        return _fail("policy_violation", str(e), violations=[str(v) for v in getattr(e, "violations", [])])
    if isinstance(e, irlpls.adapters.MissingCredentials):
        return _fail("needs_key", str(e))
    if isinstance(e, irlpls.adapters.NotSupported):
        return _fail("not_supported", str(e))
    return _fail("invalid_request", str(e))


def _errors_as_results(fn):
    """Return a request problem (unknown job, bad policy, missing key, unsupported call) to the agent as the one error
    shape instead of an opaque tool failure; a success as structuredContent."""
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            out = fn(*a, **kw)
        except (ValueError, KeyError, TypeError, irlpls.PolicyViolation, irlpls.adapters.MissingCredentials,
                irlpls.adapters.NotSupported) as e:
            return _exc_result(e)
        return out if not isinstance(out, (dict, list)) else _ok(out)
    return wrapper


def _doc(text):
    """Set a tool's description (a docstring can't be built from constants, so FastMCP would see none)."""
    def deco(fn):
        fn.__doc__ = text
        return fn
    return deco


def _env_read_only():
    return os.environ.get("IRLPLS_MCP_READ_ONLY", "").strip().lower() in ("1", "true", "yes", "on")


def build(read_only=None):
    if read_only is None:
        read_only = _env_read_only()
    instructions = INSTRUCTIONS + (READ_ONLY_NOTE if read_only else "")
    from typing import Annotated

    from pydantic import Field
    try:  # mcp >= 2
        from mcp.server.mcpserver import MCPServer as Server
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as Server

    for kw in ({"instructions": instructions, "version": irlpls.__version__}, {"version": irlpls.__version__}, {}):
        try:  # older servers take no instructions, or no version
            mcp = Server("irlpls", **kw)
            break
        except TypeError:
            continue

    Job = Annotated[str, Field(description="The job id returned by irlpls_request.")]
    Detail = Annotated[str, Field(description='"concise" (default: 8 best options, 8 closest misses plus a count of the rest) or "full".')]

    @_tool(mcp, "irlpls_request", False, "Request real-world data")
    @_errors_as_results
    @_doc("Use this when the user needs something from the physical world now or captured to order (someone go look, "
          "a photo or video of a place, drone or satellite imagery, a river level, air quality, a webcam), not archives or web facts. "
          "Ask the irlpls broker for NEW real-world data (a live reading now, or a capture made to order), in plain "
          "language. Start here. Example: need='is the Guadalupe at Comfort, TX good for tubing today', why='user deciding "
          "whether to go', policy={'max_order_usd': 0}. irlpls is inference-free: it parses the need, decides live vs "
          "commission, ranks providers by their Capture Cards and applies your policy. Nothing is ordered or called yet; "
          "call irlpls_submit next (not available on a read-only server; after irlpls_answer if a question is blocking). "
          "Unsure what can be asked? irlpls_list_capabilities. " + JOB_DOC)
    def request_data(
        need: Annotated[str, Field(description="What you need observed or captured, with the place and time in the sentence "
                                               "('river level at Comfort, TX now', 'photo of the storefront at 123 Main St, "
                                               "Springfield, IL today', '500 clips of doors opening within 7 days').")],
        why: Annotated[str, Field(description="Why it's needed (the user's decision). Optional; kept on the job.")] = "",
        place: Annotated[str, Field(description="Place name or address, if not in the need or to override it.")] = "",
        lat: Annotated[float, Field(description="Latitude, if known (overrides geocoding).")] = None,
        lon: Annotated[float, Field(description="Longitude, if known (overrides geocoding).")] = None,
        within_minutes: Annotated[int, Field(description="Deadline for delivery, in minutes from now.")] = None,
        quantity: Annotated[int, Field(description="How many items (photos, clips, responses) for a commission.")] = None,
        policy: Annotated[dict, Field(description=POLICY_DOC)] = None,
        detail: Detail = "concise",
    ) -> dict:
        hints = {k: v for k, v in dict(place=place or None, lat=lat, lon=lon, within_minutes=within_minutes, quantity=quantity).items() if v is not None}
        return _out(_track(irlpls.request(need, why=why or None, policy=policy, **hints)), detail)

    @_tool(mcp, "irlpls_answer", True, "Answer a job's questions")
    @_errors_as_results
    @_doc("Answer a job's questions (listed in its `questions`, each with a field name); the plan is rebuilt and the job "
          "returned. Pass only the fields you are answering.")
    def answer_questions(
        job_id: Job,
        place: Annotated[str, Field(description="Place name or address (answers the 'place' question).")] = None,
        lat: Annotated[float, Field(description="Latitude.")] = None,
        lon: Annotated[float, Field(description="Longitude.")] = None,
        within_minutes: Annotated[int, Field(description="Deadline, in minutes from now.")] = None,
        quantity: Annotated[int, Field(description="How many items.")] = None,
        max_order_usd: Annotated[float, Field(description="Most the user will spend on this order, in USD; 0 = free only.")] = None,
        detail: Detail = "concise",
    ) -> dict:
        fields = {k: v for k, v in dict(place=place, lat=lat, lon=lon, within_minutes=within_minutes, quantity=quantity, max_order_usd=max_order_usd).items() if v is not None}
        return _out(_job(job_id).answer(**fields), detail)

    @_tool(mcp, "irlpls_choose_provider", True, "Choose a provider")
    @_errors_as_results
    @_doc("Pick a provider from the job's plan instead of the broker's default (the best policy-compliant option this "
          "library can call). Only card_ids in `plan` can be chosen; `ruled_out` explains the others.")
    def choose_provider(job_id: Job, card_id: Annotated[str, Field(description="A card_id from the job's plan.")], detail: Detail = "concise") -> dict:
        return _out(_job(job_id).choose(card_id), detail)

    if not read_only:
        @_tool(mcp, "irlpls_submit", False, "Run a job")
        @_errors_as_results
        @_doc("Execute the job. Live routes are read immediately and free; results and any warnings come back in the job. "
              "Commissions return a quote (dry run) unless confirm=true; confirmed orders can cost money, so get the user's "
              "approval unless their policy sets a standing mandate (auto_submit with max_order_usd). Orders that violate the "
              "policy are never sent; the job ends failed or quoted-and-blocked instead.")
        def submit_job(
            job_id: Job,
            spec: Annotated[dict, Field(description="Commission only: provider-specific capture details (lat/lon are filled from "
                                                    "the request). Ignored for live routes.")] = None,
            confirm: Annotated[bool, Field(description="Commission only: true places a real order (may cost money).")] = False,
            idempotency_key: Annotated[str, Field(description="Required with confirm=true: any unique string. Reuse it on a retry so "
                                                              "the order is never placed twice.")] = None,
            detail: Detail = "concise",
        ) -> dict:
            return _out(_job(job_id).submit(spec=spec, confirm=confirm, idempotency_key=idempotency_key), detail)

    if not read_only:
        @_tool(mcp, "irlpls_approve", False, "Approve a provider's price (spends money)", destructive=False, idempotent=True,
               open_world=True)
        @_errors_as_results
        @_doc("The second, paid step of a two-step order. Some providers hold a placed order until the buyer accepts a "
              "price (Capella's cost estimate, GLOBHE's quotes); after irlpls_get_job(refresh=true) the job lists them in "
              "`approvals` [{id, amount, currency, label}]. SPENDS MONEY: call with confirm=true only after your user said yes "
              "to this order at this exact price; a standing mandate (auto_submit) never covers it. Pass approval_id, total "
              "(the amount exactly as listed) and an idempotency_key (reuse it on a retry). Without confirm it returns the "
              "call it would send. Prices not in USD are shown in their own currency and never converted; with a "
              "max_order_usd policy they are refused.")
        def approve_step(
            job_id: Job,
            approval_id: Annotated[str, Field(description="An id from the job's `approvals`.")] = None,
            confirm: Annotated[bool, Field(description="true sends the approval (spends money). Only after the user's explicit yes.")] = False,
            idempotency_key: Annotated[str, Field(description="Required with confirm=true: any unique string, reused on a retry.")] = None,
            total: Annotated[str, Field(description="Required with confirm=true when the option has an amount: that amount, exactly as listed.")] = None,
            detail: Detail = "concise",
        ) -> dict:
            j = _job(job_id)
            out = j.approve(approval_id=approval_id, confirm=confirm, idempotency_key=idempotency_key, total=total)
            if not confirm:
                d = _out(j, detail)
                d["approval_dry_run"] = dict(out, note="Nothing was sent. Ask your user to approve this exact price, then call "
                                                        "again with confirm=true, total and an idempotency_key.") if isinstance(out, dict) else out
                return d
            return _out(out, detail)

    @_tool(mcp, "irlpls_get_job", False, "Get a job")
    @_errors_as_results
    @_doc("The current job (same shape as irlpls_request returns). Not needed after a live irlpls_submit, which already "
          "returns the results. refresh=true asks the provider about a placed order and maps its status to in_flight, "
          "delivered (results attached) or failed; provider_status shows the provider's own status as sent.")
    def get_job(job_id: Job, refresh: Annotated[bool, Field(description="Ask the provider for a placed order's status.")] = False,
                detail: Detail = "concise") -> dict:
        j = _job(job_id)
        return _out(j.refresh() if refresh and not read_only else j, detail)

    @_tool(mcp, "irlpls_find_providers", True, "Find providers")
    @_errors_as_results
    @_doc("Browse without starting a job: rank Capture Cards for a need. Each result: id, name, fit (0-100, this request "
          "only), parts, gaps, what this library can call (adapter), and policy violations if a policy is given. Only "
          "providers that fit are listed; use request_data to see ruled-out providers and why.")
    def find_providers(
        need: Annotated[str, Field(description="What you need, in plain language, with place and time.")],
        limit: Annotated[int, Field(description="Most results to return.")] = 8,
        policy: Annotated[dict, Field(description=POLICY_DOC)] = None,
    ) -> dict:
        res = irlpls.find(need, limit=limit, policy=policy)
        if res and res[0].get("verdict") != "none":
            return {"results": res, "verdict": res[0].get("verdict")}
        return {"results": [], "verdict": "none",
                "message": "No provider fits (verdict none). Nothing listed does what this need asks, here. The entries under "
                           "`nearest` are the closest by score, not matches; do not present them as answers. Call "
                           "irlpls_list_capabilities to see what exists and what is unsupported.",
                "nearest": [{k: m.get(k) for k in ("id", "name", "fit", "verdict")} for m in res[:3]]}

    @_tool(mcp, "irlpls_get_card", True, "Get a Capture Card")
    @_errors_as_results
    @_doc("A provider's full Capture Card: delivery (live/commission) and speed, pricing, access cost, coverage, licensing, "
          "the 7 guarantees (evidenced / claimed / none, as stated by the provider), independent attestations, API and sources.")
    def get_card(card_id: Annotated[str, Field(description="A card id, e.g. 'usgs-water' (from a plan, ruled_out or find_providers).")]) -> dict:
        c = irlpls.card(card_id)
        if not c:
            from irlpls.broker import JobError
            raise JobError("not_found", f"no card {card_id!r} in the registry",
                           mcp="see irlpls_find_providers or irlpls_list_capabilities for card ids")
        return c

    @_tool(mcp, "irlpls_list_capabilities", True, "List what irlpls can be asked")
    @_errors_as_results
    @_doc("List what irlpls can be asked, without starting a job. Use first when you are not sure whether irlpls can answer: "
          "it returns five question types (read, watch, send, overhead, collect), the ask families under each with example "
          "needs, the providers for each (route, coverage, price, proof, whether this library can call them) and the asks "
          "irlpls cannot serve. Filter by type, family, place (country) or route. Then call irlpls_request with a need shaped "
          "like an example. Offline; nothing is logged.")
    def list_capabilities(
        type: Annotated[str, Field(description='Question type: "read", "watch", "send", "overhead" or "collect".')] = None,
        family: Annotated[str, Field(description='A family id, e.g. "air-quality" (see the unfiltered index).')] = None,
        place: Annotated[str, Field(description='Country name or ISO code, e.g. "FI" or "US-CA".')] = None,
        route: Annotated[str, Field(description='"live" (already reporting) or "commission" (captured to order).')] = None,
        free_only: Annotated[bool, Field(description="Only providers free to use.")] = False,
        detail: Annotated[str, Field(description='"index" (default: families with one-line provider rows) or "providers" (adds full provider records, capped at 40).')] = "index",
    ) -> dict:
        out = catalog.query(type=type, family=family, place=place, route=route, free_only=free_only, detail=detail)
        if isinstance(out, dict) and out.get("error"):
            return _fail("invalid_arguments", out["error"], "Use one of the values listed in valid.",
                         **{k: v for k, v in out.items() if k != "error"})
        return out

    _protocol_tools(mcp, read_only)
    _resources(mcp)
    _output_schemas(mcp)
    _arguments_as_errors(mcp)
    return mcp


# -- outputSchema per tool (STANDARD 14.10): the matching schema, named by its $id; errors are isError results ---------

_SCHEMA = "https://irlpls.com/2026-10-26/schemas/"
_MONEY = {"type": "object", "required": ["amount", "currency"],
          "properties": {"amount": {"type": "string", "pattern": "^[0-9]+(\\.[0-9]+)?$"}, "currency": {"type": "string"}}}
JOB_OUTPUT = {
    "type": "object", "title": "irlpls Job", "description": "A Job (" + _SCHEMA + "job.json).",
    "required": ["id", "state", "plan"],
    "properties": {
        "id": {"type": "string"},
        "state": {"enum": ["clarifying", "proposed", "quoted", "submitted", "in_flight", "delivered", "failed"]},
        "best_card_id": {"type": ["string", "null"], "description": "The option irlpls_submit runs now (chosen, else the default); "
                         "the same option has default: true in plan."},
        "request": {"type": ["object", "null"]}, "policy": {"type": ["object", "null"]},
        "questions": {"type": "array", "items": {"type": "object", "required": ["field", "ask", "blocking"]}},
        "plan": {"type": "array", "items": {"type": "object", "required": ["card_id", "route", "fit"]}},
        "ruled_out": {"type": "array"}, "ruled_out_more": {"type": "object", "additionalProperties": {"type": "integer"}},
        "quote": {"type": ["object", "null"], "properties": {"price": {"anyOf": [{"type": "null"}, dict(_MONEY, properties=dict(
            _MONEY["properties"], firm={"type": "boolean"}))]}}},
        "approvals": {"type": "array"}, "results": {"type": "array"}, "warnings": {"type": "array"}, "log": {"type": "array"},
        "approval_dry_run": {"type": "object"},
    },
}
_OBJ = lambda title, ref, req=(): {"type": "object", "title": title, "description": f"{title} ({_SCHEMA}{ref}).",
                                   **({"required": list(req)} if req else {})}
OUTPUT_SCHEMAS = {
    "irlpls_request": JOB_OUTPUT, "irlpls_answer": JOB_OUTPUT, "irlpls_choose_provider": JOB_OUTPUT,
    "irlpls_submit": JOB_OUTPUT, "irlpls_approve": JOB_OUTPUT, "irlpls_get_job": JOB_OUTPUT,
    "irlpls_find_providers": {"type": "object", "title": "Ranked providers", "required": ["results", "verdict"],
                              "properties": {"results": {"type": "array", "items": {"type": "object", "required": ["id", "fit"]}},
                                             "verdict": {"enum": ["strong", "weak", "none"]}, "message": {"type": "string"},
                                             "nearest": {"type": "array"}}},
    "irlpls_get_card": _OBJ("Capture Card", "capture-card.json", ("id", "name")),
    "irlpls_list_capabilities": {"type": "object", "title": "Catalog query", "required": ["families"],
                                 "properties": {"families": {"type": "array"}, "unsupported": {"type": "array"}}},
    "irlpls_discover": _OBJ("Capture Card or a router's {cards}", "capture-card.json"),
    "irlpls_quote": _OBJ("Quote", "protocol/quote.json#/$defs/quote", ("id", "offers")),
    "irlpls_order": _OBJ("Order", "protocol/order.json#/$defs/order", ("id", "state")),
    "irlpls_status": _OBJ("Order", "protocol/order.json#/$defs/order", ("id", "state")),
    "irlpls_cancel": _OBJ("Order", "protocol/order.json#/$defs/order", ("id", "state")),
    "irlpls_deliver": _OBJ("Delivery", "protocol/delivery.json", ("items",)),
}
ERROR_SHAPE = "{code, message, hint, ...}; protocol problems add RFC 9457 type, title, status, detail"


def _output_schemas(mcp):
    """Publish each tool's outputSchema. Results are built by the tools themselves (structuredContent), so the SDK does
    not re-validate them; error results (isError) carry the error shape instead."""
    tm = getattr(mcp, "_tool_manager", None)
    for name, schema in OUTPUT_SCHEMAS.items():
        t = tm.get_tool(name) if tm is not None and hasattr(tm, "get_tool") else None
        if t is None:
            continue
        try:
            t.fn_metadata.output_model = None
            t.fn_metadata.output_schema = schema
            t.__dict__.pop("output_schema", None)   # a cached_property read before this point
        except Exception:  # an SDK that doesn't allow it: no outputSchema, results unchanged
            pass


def _arguments_as_errors(mcp):
    """Arguments of the wrong type (pydantic) come back in the one error shape, not as an SDK traceback string."""
    orig = getattr(mcp, "call_tool", None)
    if orig is None:
        return

    async def call_tool(name, arguments, context=None, *a, **kw):
        try:
            if context is None and not a and not kw:
                return await orig(name, arguments)   # mcp 1.x FastMCP takes no context here
            return await orig(name, arguments, context, *a, **kw)
        except Exception as e:
            cause = e.__cause__
            errs = getattr(cause, "errors", None)
            if callable(errs):
                try:
                    fields = sorted({".".join(str(x) for x in er.get("loc", ())) + ": " + str(er.get("msg", "")) for er in errs()})
                except Exception:
                    fields = []
                return _fail("invalid_arguments", f"{name}: " + ("; ".join(fields) or "invalid arguments"),
                             errors=[{"path": f.split(":")[0], "detail": f.split(": ", 1)[-1]} for f in fields])
            if type(e).__name__ in ("ToolError",) and "Unknown tool" in str(e):
                return _fail("not_found", str(e), "Call one of the tools in tools/list.")
            raise
    mcp.call_tool = call_tool


# -- wire-protocol tools (PROTOCOL-DRAFT section 9) ---------------------------------------------------------------------

_rquotes = collections.OrderedDict()   # router quote id -> {"job_id", "quote", "offers": {offer_id: provider offer or None}}
_orders = collections.OrderedDict()    # provider order id -> job id
_order_keys = {}                       # idempotency key -> (hash of the order request, order id): replay, never reorder
_order_lock = __import__("threading").RLock()   # one order at a time, so a concurrent retry with the same key replays


def _problem(code, detail=None, status=None, **extra):
    from irlpls.protocol.client import ProtocolError
    p = dict(extra, code=code, **({"detail": detail} if detail else {}))
    return ProtocolError(p, status or {"not_found": 404, "idempotency_key_required": 400, "unsupported_version": 400,
                                       "invalid_request": 400, "idempotency_key_reused": 422}.get(code, 422)).problem


PROBLEM_HINTS = {
    "needs_input": "Answer the questions listed, then quote again.",
    "idempotency_key_required": "Pass meta.idempotency_key (any unique string; reuse it on a retry).",
    "idempotency_key_reused": "Use a new idempotency_key for a different order.",
    "unsupported_version": "Send meta.irlpls_version as one of supported_versions.",
    "quote_expired": "Call irlpls_quote again.",
    "no_offer": "See ruled_out; try irlpls_find_providers or irlpls_list_capabilities.",
    "no_protocol": "Order it with irlpls_submit (irlpls_request, then confirm=true), or through the provider's own API.",
    "over_max_total": "Raise constraints.max_total, or pick a cheaper offer.",
    "price_changed": "Ask the person to approve the new price, then order again.",
}


def _error_result(problem, shaped=False):
    """A problem as a tool result with isError and the problem as structuredContent (falls back to the dict). Every
    error carries code, message and hint; a protocol problem keeps its RFC 9457 fields too."""
    import json
    problem = dict(problem)
    if not shaped:
        problem.setdefault("message", problem.get("detail") or problem.get("title") or problem.get("code") or "error")
        problem.setdefault("hint", PROBLEM_HINTS.get(problem.get("code")) or HINTS.get(problem.get("code"), ""))
    problem = _jsonable(problem)
    try:
        from mcp.types import CallToolResult, TextContent
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(problem))], structured_content=problem, is_error=True)
    except Exception:  # an SDK without these types: the problem object itself
        return problem


def _protocol_errors(fn):
    """ProtocolError (a provider's or ours), ValueError and the like become problem results, never opaque failures."""
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        from irlpls.protocol.client import ProtocolError
        try:
            out = fn(*a, **kw)
        except ProtocolError as e:
            return _error_result(e.problem)
        except (ValueError, KeyError, TypeError, irlpls.PolicyViolation) as e:
            from irlpls.broker import JobError
            extra = {"hint": e.hint("mcp")} if isinstance(e, JobError) else {}
            return _error_result(_problem(getattr(e, "code", None) if isinstance(e, JobError) else "invalid_request", str(e)[:300], 400, **extra))
        if isinstance(out, dict) and out.get("type", "").startswith("https://irlpls.com/") and "/problems/" in out.get("type", ""):
            return _error_result(out)
        return _ok(out) if isinstance(out, dict) else out
    return wrapper


def _meta(meta, need_key=False):
    from irlpls.protocol.client import VERSION, http_key
    meta = meta or {}
    v = meta.get("irlpls_version")
    if v and v != VERSION:
        return None, _problem("unsupported_version", f"this server speaks {VERSION}", supported_versions=[VERSION])
    key = meta.get("idempotency_key")
    if need_key and not key:
        return None, _problem("idempotency_key_required", "meta.idempotency_key is required (reuse it on retry)")
    if key:
        try:
            http_key(key)
        except ValueError as e:
            return None, _problem("invalid_request", str(e))
    return key, None


def _remember(store, k, v, cap=MAX_JOBS):
    store[k] = v
    while len(store) > cap:
        store.popitem(last=False)


def _internal_request(r):
    """A wire Request as the broker reads it: no policy or why; ISO country codes in the parser's lower case."""
    req = {k: v for k, v in r.items() if k not in ("policy", "why")}
    if isinstance(req.get("country_code"), str):
        req["country_code"] = req["country_code"].lower()
    return req


def _policy_from_constraints(c):
    """Constraints back into a Policy the broker enforces (the inverse of broker._constraints)."""
    from irlpls.protocol.client import amount
    c = c or {}
    pol = {}
    if c.get("max_total") is not None:
        a = amount(c["max_total"])
        if a is None or c["max_total"].get("currency") != "USD":
            raise ValueError("constraints.max_total must be money in USD, e.g. {\"amount\": \"40.00\", \"currency\": \"USD\"}")
        pol["max_order_usd"] = a  # Decimal end to end: never float(a)
    rights = c.get("rights_needed") or []
    pol["need_ai_training"] = "ai_training" in rights
    pol["need_resale"] = "resale" in rights
    if c.get("proof"):
        pol["require"] = dict(c["proof"])
    return irlpls.Policy.from_dict(pol)


def _indicative_offer(job, opt, card):
    """An offer built from the card for a provider that doesn't speak the protocol: firm false, no order_url."""
    from irlpls.protocol.client import money
    lic = card.get("licensing") or {}
    o = {"id": opt["card_id"], "provider": {"card_id": opt["card_id"], "name": opt["name"]}, "route": opt["route"],
         "firm": False, "quantity": job.request.quantity or 1, "fit": opt["fit"],
         "proof": {k: v["level"] for k, v in (card.get("verification") or {}).items() if isinstance(v, dict) and "level" in v},
         "rights": {k: lic[k] for k in ("ai_training", "resale") if lic.get(k) in ("allowed", "forbidden")}}
    if opt["price"] is not None:
        o["unit_price"] = money(opt["price"])
        o["total"] = money(opt["price"] * (job.request.quantity or 1))
        if opt.get("price_unit"):
            o["unit"] = str(opt["price_unit"])[:80]
    if str(opt.get("continue_url") or "").startswith("https://"):
        o["continue_url"] = opt["continue_url"]
    return o


def _router_quote(job, card_id=None, limit=3, received=None):
    """Quote the plan: protocol providers at their own endpoints (exact place only for a provider named by card_id),
    the others indicatively from their cards; violations and problems go to ruled_out, never dropped silently.
    An offer from a protocol provider carries that provider's own quote (provider_quote_id, provider_quote_url) and
    its order_url; orders re-quote at the provider with the exact request first (STANDARD 14.11). request_sha256 is
    the hash of the request this router received (`received`)."""
    import time as _t

    from irlpls.protocol.client import ProtocolError, new_key, request_sha256
    offers, providers, ruled = [], {}, []
    opts = [o for o in job.plan if card_id in (None, o["card_id"])]
    for opt in opts[: (len(opts) if card_id else limit)]:
        card = irlpls.card(opt["card_id"])
        if opt["policy"]:
            codes = sorted({code for code, _ in job.policy.violations(card)}) if job.policy else ["policy:require"]
            ruled.append({"card_id": opt["card_id"], "name": opt["name"], "reasons": codes or ["policy:require"],
                          "why": "; ".join(opt["policy"])[:300]})
            continue
        if opt["sdk"].get("protocol"):
            try:
                q, offer, bad, _ = job.protocol_quote(opt["card_id"], exact=bool(card_id))
            except ProtocolError as e:
                ruled.append({"card_id": opt["card_id"], "name": opt["name"], "reasons": [e.code if e.code != "unsafe_endpoint" else "provider_error"],
                              "why": f"the provider answered {e.code}"})
                continue
            if bad:
                codes = sorted({b.split(":")[0] + ":" + b.split(":")[1] if b.startswith("policy:") else "provider_error" for b in bad})
                ruled.append({"card_id": opt["card_id"], "name": opt["name"], "reasons": codes, "why": "; ".join(bad)[:300]})
                continue
            o = dict(offer, id=opt["card_id"], fit=opt["fit"])
            o["order_url"] = opt["sdk"]["protocol"] + "/orders"  # the provider card's endpoint, never rewritten elsewhere
            if isinstance(q.get("id"), str):
                from urllib.parse import quote as _pq
                o["provider_quote_id"] = q["id"]
                o["provider_quote_url"] = opt["sdk"]["protocol"] + "/quotes/" + _pq(q["id"], safe="")
            providers[o["id"]] = {"offer": offer, "quote_id": q.get("id")}
        else:
            o = _indicative_offer(job, opt, card)
            providers[o["id"]] = None
        offers.append(o)
    for r in job.ruled_out[:20]:
        ruled.append({"card_id": r["card_id"], "name": r["name"], "reasons": r["reasons"], "why": r.get("why", "")}
                     | ({"continue_url": r["continue_url"]} if str(r.get("continue_url") or "").startswith("https://") else {}))
    if not offers:
        return _problem("no_offer", "no provider fits this request", ruled_out=ruled)
    now = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())
    exp = [p["offer"].get("expires_at") for p in providers.values() if p and p["offer"].get("expires_at")]
    quote = {"id": "rq_" + new_key().replace("-", "")[:16], "created_at": now,
             "request_sha256": request_sha256(received if received is not None else job._wire_request(True)),
             "offers": offers, "ruled_out": ruled}
    quote["expires_at"] = min(exp) if exp else _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(_t.time() + 3600))
    nb = [q for q in job.questions if not q.get("blocking")]
    if nb:
        quote["questions"] = nb
    _remember(_rquotes, quote["id"], {"job_id": job.id, "quote": quote, "providers": providers})
    return quote



def _job_order(job):
    """The Order a job holds, or the problem that explains why there is none."""
    if job.protocol and job.protocol.get("order"):
        _remember(_orders, job.protocol["order"]["id"], job.id)
        return job.protocol["order"]
    if job.error:
        return dict(job.error)
    blocked = (job.quote or {}).get("blocked_by_policy")
    if blocked:
        first = str(blocked[0])
        code = ":".join(first.split(":")[:2]) if first.startswith("policy:") else ("price_changed" if "price rose" in first else "over_max_total")
        return _problem(code, "Not sent: " + "; ".join(map(str, blocked))[:300], violations=[str(b) for b in blocked])
    return _problem("provider_error", f"no order was placed (job {job.state}); see irlpls_get_job {job.id}")


def _order_job(order_id):
    jid = _orders.get(order_id)
    j = irlpls.get_job(jid) if jid else None
    if not j:
        return None, _problem("not_found", f"no order {order_id} placed through this server", 404, order_id=order_id)
    return j, None


def _protocol_tools(mcp, read_only):
    from typing import Annotated

    from pydantic import Field
    if read_only:  # a read-only server makes no provider calls: quoting or discovering would relay people's asks
        return
    Meta = Annotated[dict, Field(description='{"irlpls_version": "2026-10-26", "idempotency_key": "..."}. '
                                             "idempotency_key is required for irlpls_order and irlpls_cancel; reuse it on retry.")]
    OrderId = Annotated[str, Field(description="The order id from irlpls_order.")]

    @_tool(mcp, "irlpls_discover", True, "Discover a provider's card")
    @_protocol_errors
    @_doc("Fetch a provider's Capture Card from its own domain (GET https://<domain>/.well-known/irlpls) and check its "
          "protocol block: endpoint on the provider's domain, keys on its origin. Returns the card (or a router's "
          "{cards: [...]}). Use irlpls_get_card for a card already in the registry.")
    def discover(domain: Annotated[str, Field(description="The provider's domain, e.g. 'streetlens.example'.")], meta: Meta = None) -> dict:
        from irlpls import broker
        from irlpls.protocol.client import discover as _discover
        _, bad = _meta(meta)
        if bad:
            return bad
        o = broker.PROTOCOL.get("client") or {}
        return _discover(domain, allow_private=o.get("allow_private", False), allow_http=o.get("allow_http", False))

    @_tool(mcp, "irlpls_quote", True, "Quote real-world data")
    @_protocol_errors
    @_doc("Get offers for a request without ordering anything (no money, no dispatch). quote_request = {request: an irlpls "
          "Request (need, place, lat, lon, within_minutes, quantity, ...; no policy), constraints: {max_total: {amount: "
          "'40.00', currency: 'USD'}, rights_needed, proof, deliver_by}}. Acts as a router: providers that speak the "
          "protocol are quoted at their own endpoint (firm offers carry order_url and expires_at); others get an "
          "indicative offer from their card (firm false). Pass card_id to quote one provider only, or quote_id to read a "
          "quote again. Blocking questions come back as a needs_input problem: re-quote with the answers.")
    def quote(quote_request: Annotated[dict, Field(description="{request, constraints?}.")] = None,
              quote_id: Annotated[str, Field(description="Read back a quote this server returned.")] = None,
              card_id: Annotated[str, Field(description="Quote only this provider (from irlpls_find_providers or a plan).")] = None,
              meta: Meta = None) -> dict:
        _, bad = _meta(meta)
        if bad:
            return bad
        if quote_id:
            q = _rquotes.get(quote_id)
            return q["quote"] if q else _problem("not_found", f"no quote {quote_id}", 404, quote_id=quote_id)
        if not isinstance(quote_request, dict) or not isinstance(quote_request.get("request"), dict) \
                or not quote_request["request"].get("need"):
            return _problem("invalid_request", "quote_request.request.need is required", 400, errors=[{"path": "$.request.need"}])
        req = _internal_request(quote_request["request"])
        job = _track(irlpls.request(req, policy=_policy_from_constraints(quote_request.get("constraints"))))
        if job.state == "clarifying":
            return _problem("needs_input", "answer these, then quote again",
                            questions=[{k: q[k] for k in ("field", "ask", "blocking", "why") if k in q} for q in job.questions if q["blocking"]])
        if card_id and not any(o["card_id"] == card_id for o in job.plan):
            r = next((o for o in job.ruled_out if o["card_id"] == card_id), None)
            return _problem("no_offer", f"{card_id} is not an option for this request",
                            ruled_out=[{"card_id": card_id, "reasons": r["reasons"] if r else ["no_match"], "why": (r or {}).get("why", "")}])
        return _router_quote(job, card_id, received=quote_request["request"])

    @_tool(mcp, "irlpls_order", False, "Order real-world data (may cost money)", destructive=False, idempotent=True, open_world=True)
    @_protocol_errors
    @_doc("Place an order. MAY COST MONEY AND MAY DISPATCH A PERSON: get the user's approval of the offer's total first "
          "(or rely on their standing mandate). order_request = {quote_id, offer_id} from irlpls_quote, or a direct "
          "{request, constraints: {max_total}} together with card_id. The order goes to the provider's own endpoint, "
          "never through irlpls. meta.idempotency_key is required; reuse it on a retry so nothing is ordered twice. "
          "Returns the provider's Order (state submitted, in_flight, delivered or failed with reason_code); then poll "
          "irlpls_status and read irlpls_deliver.")
    def order(order_request: Annotated[dict, Field(description="{quote_id, offer_id} or {request, constraints: {max_total}}.")],
              meta: Meta = None,
              card_id: Annotated[str, Field(description="Direct orders only: the provider to order from.")] = None) -> dict:
        key, bad = _meta(meta, need_key=True)
        if bad:
            return bad
        with _order_lock:
            return _place(order_request, key, card_id)

    def _place(order_request, key, card_id):
        import hashlib

        from irlpls.protocol.client import jcs
        fp = hashlib.sha256(jcs({"order_request": order_request, "card_id": card_id})).hexdigest()
        if key in _order_keys:
            h, oid = _order_keys[key]
            if h != fp:
                return _problem("idempotency_key_reused", "this idempotency_key was used with a different order request")
            j, err = _order_job(oid)
            return err or j.protocol["order"]
        if not isinstance(order_request, dict):
            return _problem("invalid_request", "order_request must be an object", 400)
        if order_request.get("quote_id"):
            rq = _rquotes.get(order_request["quote_id"])
            if not rq:
                return _problem("quote_expired", "unknown or expired quote; quote again", 410, quote_id=order_request["quote_id"])
            oid = order_request.get("offer_id")
            if oid not in rq["providers"]:
                return _problem("not_found", f"no offer {oid} in quote {order_request['quote_id']}", 404)
            if rq["providers"][oid] is None:
                return _problem("no_protocol", f"{oid} does not speak the irlpls protocol; order it with irlpls_submit "
                                               "(irlpls_request, then confirm=true) or through the provider's own API")
            job = irlpls.get_job(rq["job_id"])
            if job is None:
                return _problem("quote_expired", "the quote's job is gone; quote again", 410, quote_id=order_request["quote_id"])
            job.choose(oid)
            job.protocol["offer"] = rq["providers"][oid]["offer"]  # the price the person approved: a higher re-quote is refused
        else:
            if not isinstance(order_request.get("request"), dict) or not (order_request.get("constraints") or {}).get("max_total"):
                return _problem("invalid_request", "a direct order needs request and constraints.max_total", 400)
            req = _internal_request(order_request["request"])
            job = _track(irlpls.request(req, policy=_policy_from_constraints(order_request["constraints"])))
            if job.state == "clarifying":
                return _problem("needs_input", "answer these first",
                                questions=[{k: q[k] for k in ("field", "ask", "blocking", "why") if k in q} for q in job.questions if q["blocking"]])
            if card_id:
                job.choose(card_id)
            opt = job.choice or job.best()
            if not opt or not opt["sdk"].get("protocol"):
                return _problem("no_protocol", "no provider for this request speaks the irlpls protocol; use irlpls_submit")
        job.submit(confirm=True, idempotency_key=key)
        out = _job_order(job)
        if job.protocol and job.protocol.get("order"):
            _order_keys[key] = (fp, out["id"])
        return out

    @_tool(mcp, "irlpls_status", True, "Order status")
    @_protocol_errors
    @_doc("The provider's current Order for an order placed with irlpls_order (asks the provider). Poll no faster than "
          "its Retry-After (60 s by default for commissions). State is submitted, in_flight, delivered or failed (with reason_code).")
    def status(order_id: OrderId, meta: Meta = None) -> dict:
        _, bad = _meta(meta)
        if bad:
            return bad
        j, err = _order_job(order_id)
        return err or (j.refresh() and j.protocol["order"])

    @_tool(mcp, "irlpls_deliver", True, "Order delivery")
    @_protocol_errors
    @_doc("The Delivery for an order (also while in_flight, with complete false): items with media hashes, per-stage "
          "provenance (what the provider evidences or only claims, never merged) and relays. "
          "x-com.irlpls.receipt_checks gives each item's receipt check against the provider card's keys.")
    def deliver(order_id: OrderId, meta: Meta = None) -> dict:
        _, bad = _meta(meta)
        if bad:
            return bad
        j, err = _order_job(order_id)
        if err:
            return err
        cl = j._pclient(j.protocol["card_id"])
        d = cl.delivery(order_id)
        j._take_delivery(d)
        return dict(d, **{"x-com.irlpls.receipt_checks": [o["receipt_check"] for o in j.results]})

    @_tool(mcp, "irlpls_cancel", False, "Cancel an order", destructive=True, idempotent=True, open_world=True)
    @_protocol_errors
    @_doc("Ask the provider to stop an order (cooperative: it may stay in_flight, then end failed with reason_code "
          "cancelled, or delivered if too late; refunds follow the provider's terms). meta.idempotency_key is required.")
    def cancel(order_id: OrderId, meta: Meta = None) -> dict:
        key, bad = _meta(meta, need_key=True)
        if bad:
            return bad
        j, err = _order_job(order_id)
        if err:
            return err
        j.error = None
        j.cancel(idempotency_key=key)
        return dict(j.error) if j.error else j.protocol["order"]


def _resources(mcp):
    """irlpls://catalog (index), irlpls://catalog/full and irlpls://catalog/family/{id}, where the SDK supports resources."""
    import json
    res = getattr(mcp, "resource", None)
    if res is None:
        return

    def dump(o):
        return json.dumps(o, separators=(",", ":"), ensure_ascii=False)
    try:
        @res("irlpls://catalog", name="irlpls_catalog", title="irlpls catalog index", mime_type="application/json",
             description="What irlpls can be asked: question types, families, one-line provider rows, unsupported asks.")
        def catalog_index() -> str:
            return dump(catalog.index_of(catalog.load()))

        @res("irlpls://catalog/full", name="irlpls_catalog_full", title="irlpls catalog (full)", mime_type="application/json",
             description="The index plus a full record per provider (about 120 KB).")
        def catalog_full() -> str:
            return dump(catalog.load())

        @res("irlpls://catalog/family/{id}", name="irlpls_catalog_family", title="One family with its provider records",
             mime_type="application/json")
        def catalog_family(id: str) -> str:
            cat = catalog.load()
            f = next((f for f in cat["families"] if f["id"] == id), None)
            if not f:
                raise ValueError(f"no family {id}; valid: {[x['id'] for x in cat['families']]}")
            return dump({**f, "providers": {i: cat["providers"][i] for i in f["providers"]}})
    except TypeError:  # an SDK whose resource() takes other arguments
        return


def serve(mcp, transport="stdio", host="127.0.0.1", port=8765):
    """Run on stdio (default) or streamable HTTP through the SDK's own mechanism (mcp >= 2 and FastMCP 1.x)."""
    if transport == "stdio":
        return mcp.run()
    if transport != "streamable-http":
        raise SystemExit(f"irlpls-mcp: unknown transport {transport!r} (use stdio or streamable-http)")
    try:
        settings = getattr(mcp, "settings", None)
        if settings is not None and hasattr(settings, "host"):      # mcp 1.x FastMCP: host and port are settings
            settings.host, settings.port = host, port
            return mcp.run(transport="streamable-http")
        return mcp.run(transport="streamable-http", host=host, port=port)  # mcp >= 2
    except (TypeError, ValueError, AttributeError) as e:
        raise SystemExit(f"irlpls-mcp: this mcp SDK does not support streamable-http ({e}); upgrade with: pip install -U mcp")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="irlpls-mcp", description="irlpls MCP server (stdio by default).")
    ap.add_argument("--read-only", action="store_true", help="plan only: do not register irlpls_submit, never execute or refresh")
    ap.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    ap.add_argument("--host", default="127.0.0.1", help="streamable-http bind address (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8765, help="streamable-http port (default 8765)")
    ap.add_argument("--allow-remote", action="store_true",
                    help="required to bind a non-loopback --host; the HTTP transport has no authentication")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    read_only = a.read_only or _env_read_only()
    if a.transport == "streamable-http" and a.host not in ("127.0.0.1", "localhost", "::1"):
        if not a.allow_remote:
            ap.error(f"refusing to bind {a.host}: streamable-http has no authentication, so anyone who can reach it could "
                     + ("make plans on this read-only server" if read_only else
                        "call irlpls_order with this process's provider credentials")
                     + ". Pass --allow-remote to do it anyway (behind your own authenticating proxy).")
        print(remote_warning(a.host, read_only), file=sys.stderr)
    serve(build(read_only=read_only), a.transport, a.host, a.port)


def remote_warning(host, read_only):
    """The startup warning for a non-loopback bind; it must say what this server can actually do."""
    if read_only:
        return (f"irlpls-mcp: WARNING: serving on {host} with no authentication (read-only). Anyone who can reach this "
                "port can make plans; this server cannot quote or order and makes no provider calls.")
    return (f"irlpls-mcp: WARNING: serving on {host} with no authentication in FULL mode. Anyone who can reach this port "
            "can use the ordering tools (irlpls_submit, irlpls_quote, irlpls_order) and spend with this process's "
            "provider credentials. Use --read-only, or put an authenticating proxy in front.")


if __name__ == "__main__":
    main()
