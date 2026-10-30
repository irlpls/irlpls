"""End-to-end demo of the irlpls wire protocol, all on localhost, nothing published, no real order placed.

    uv run --with cryptography --with jsonschema python tools/protocol_demo.py            # live USGS if reachable
    uv run --with cryptography --with jsonschema python tools/protocol_demo.py --fast     # no network, ~5 s
    ... --no-write    print only; default writes docs/protocol/DEMO.md

What it runs (docs/protocol/PROTOCOL-DRAFT.md):
  1. Two providers on 127.0.0.1:
     - the reference simulated provider: irlpls.protocol.server.ProviderServer + SimFulfiller (fake photos, never real);
     - usgs-water behind the adapter shim (irlpls.protocol.shim.serve_adapter), signing custody receipts with a key made
       for this run. It calls the live USGS API when reachable, else answers from a recording embedded below (taken from
       the live API on 2026-10-28), and the transcript says which.
  2. 'river level at Comfort, TX now': router plan -> discover -> quote -> order -> deliver -> verify receipts.
  3. 'photo of the storefront at 12 Elm St' against the simulator: router plan -> discover -> quote -> order with an
     Idempotency-Key -> replay (same order) -> poll status -> deliver -> verify receipt -> tampered receipt fails ->
     a person-targeting ask is refused (by the router, by the provider, and by the shim).
  4. A timestamped transcript, printed and written to docs/protocol/DEMO.md with what each step proves.

`run(fast=True, out=None)` is what tests/test_protocol_demo.py calls: recorded USGS data, a 1-second simulator, short
polls, no outbound network (only the two localhost servers are contacted).
"""
import argparse
import contextlib
import copy
import json
import pathlib
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import irlpls  # noqa: E402
from irlpls import broker  # noqa: E402
from irlpls import http as ihttp  # noqa: E402
from irlpls.adapters import usgs_water  # noqa: E402
from irlpls.protocol import client as pc  # noqa: E402
from irlpls.protocol import shim as S  # noqa: E402
from irlpls.protocol.server import ProviderServer, SimFulfiller  # noqa: E402

OUT = ROOT / "docs" / "protocol" / "DEMO.md"
LOCAL = {"allow_http": True, "allow_private": True}   # the explicit opt-out for plain http on 127.0.0.1

RIVER_NEED = "river level at Comfort, TX now"
RIVER_HINTS = dict(geocode=False, lat=29.9683, lon=-98.9054, country_code="us", state="texas")
SHOP_NEED = "photo of the storefront at 12 Elm St"
SHOP_HINTS = dict(geocode=False, lat=39.80168, lon=-89.64357, country_code="us", state="illinois",
                  place="12 Elm St, Springfield, IL 62701", within_minutes=480)
PERSON_NEED = "photo of the woman leaving 12 Elm St every morning"

# ---------------------------------------------------------------------------------------- recorded USGS (2026-10-28)
# Trimmed from the live API at 2026-10-28T08:47Z: the gauges near Comfort, TX, the daily statistics for 28 October,
# and the newest river-camera frame name. The frame's bytes are not stored; recorded mode serves a stand-in.
REC_AT = "2026-10-28T08:47Z"
REC_GAUGES = {
    "00065": [([-98.89716666666668, 29.965238888888887], "USGS-08167000", "3.27", "ft"),
              ([-98.97413611111111, 30.01143611111111], "USGS-08166700", "5.15", "ft")],
    "00060": [([-98.89716666666668, 29.965238888888887], "USGS-08167000", "78.4", "ft^3/s")],
}
REC_TIME = "2026-10-28T08:35:00+00:00"
# The statistics: USGS Water Data Statistics API observationNormals (day-of-year percentiles of the daily mean) for
# 28 October, recorded live 2026-10-29T02:35Z (WaterServices' nwis/stat is being shut, 2027-02-22).
REC_STATS = {"type": "FeatureCollection", "next": None, "features": [{"type": "Feature", "properties": {
    "monitoring_location_id": "USGS-08167000", "monitoring_location_name": "Guadalupe Rv at Comfort, TX", "data": [
        {"parameter_code": "00060", "unit_of_measure": "ft^3/s", "parent_statistic_id": "00003", "values": [
            {"time_of_year": "10-28", "time_of_year_type": "day_of_year", "computation": "percentile", "sample_count": 87,
             "percentiles": ["5", "10", "25", "50", "75", "90", "95"], "values": ["17.7", "30.4", "57.7", "104.0", "185.0", "426.8", "646.0"]}]},
        {"parameter_code": "00065", "unit_of_measure": "ft", "parent_statistic_id": "00003", "values": [
            {"time_of_year": "10-28", "time_of_year_type": "day_of_year", "computation": "percentile", "sample_count": 36,
             "percentiles": ["5", "10", "25", "50", "75", "90", "95"], "values": ["0.753", "1.094", "2.192", "2.79", "3.38", "3.679", "5.683"]}]}]}}]}
REC_CAMERA = {"camId": "TX_Guadalupe_Rv_at_Comfort", "nwisId": "08167000", "camName": "Guadalupe Rv at Comfort",
              "lat": "29.965238888888887", "lng": "-98.89716666666668", "hideCam": False,
              "smallDir": "https://usgs-nims-images.s3.amazonaws.com/720/TX_Guadalupe_Rv_at_Comfort/"}
REC_FRAME = "TX_Guadalupe_Rv_at_Comfort___2026-10-28T08-41-21Z.jpg"
REC_JPEG = b"\xff\xd8\xff\xe0 recorded-mode stand-in for the USGS camera frame (bytes not stored) \xff\xd9"


def _recorded_get(url, params=None, **kw):
    params = params or {}
    if "latest-continuous" in url:
        return {"features": [{"geometry": {"type": "Point", "coordinates": xy}, "properties": {
            "monitoring_location_id": site, "time": REC_TIME, "value": v, "unit_of_measure": u, "approval_status": "Provisional"}}
            for xy, site, v, u in REC_GAUGES.get(params.get("parameter_code"), [])]}
    if "observationNormals" in url:
        return REC_STATS
    if url.endswith("/cameras"):
        return [REC_CAMERA]
    if "listFiles" in url:
        return [REC_FRAME]
    raise ihttp.HTTPError(599, url, "not in the recording")


def _recorded_media(url):
    if not url.startswith("https://usgs-nims-images.s3.amazonaws.com/"):
        raise ValueError("not in the recording")
    return REC_JPEG, "image/jpeg"


def usgs_reachable(timeout=8):
    """(True, note) when the live USGS OGC API answers for Comfort, TX within `timeout`; else (False, why)."""
    try:
        d = ihttp.get(f"{usgs_water.BASE}/collections/latest-continuous/items", timeout=timeout,
                      params={"bbox": "-98.95,29.93,-98.85,30.0", "parameter_code": "00065", "f": "json", "limit": 5})
        n = len((d or {}).get("features") or [])
        return (True, f"live USGS API answered ({n} gauge feature(s) near Comfort)") if n else (False, "live USGS API returned no gauges")
    except Exception as e:  # noqa: BLE001 - any failure means: use the recording
        return False, f"live USGS API unreachable ({type(e).__name__}: {str(e)[:80]})"


@contextlib.contextmanager
def recorded_usgs():
    old_get, old_cams = ihttp.get, usgs_water._cameras
    ihttp.get, usgs_water._cameras = _recorded_get, None
    try:
        yield
    finally:
        ihttp.get, usgs_water._cameras = old_get, old_cams


# ------------------------------------------------------------------------------------------------------- transcript

class Transcript:
    def __init__(self):
        self.t0 = time.monotonic()
        self.started = datetime.now(timezone.utc)
        self.lines, self.steps = [], []

    def _stamp(self):
        return f"[+{time.monotonic() - self.t0:6.2f}s]"

    def step(self, title, proves):
        at = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"
        n = len(self.steps) + 1
        self.steps.append({"n": n, "title": title, "proves": proves, "at": at, "ok": None})
        self.out("")
        self.out(f"== {n}. {title}  ({at})", stamp=False)

    def out(self, msg="", stamp=True):
        line = f"{self._stamp()} {msg}" if stamp and msg else msg
        self.lines.append(line)
        print(line, flush=True)

    def check(self, ok, msg):
        """Record a verdict for the current step; the demo keeps going so the transcript shows every result."""
        self.out(("PASS  " if ok else "FAIL  ") + msg)
        st = self.steps[-1]
        st["ok"] = bool(ok) if st["ok"] is None else st["ok"] and bool(ok)
        return ok

    @property
    def text(self):
        return "\n".join(self.lines)


def _j(obj, n=160):
    s = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    return s if len(s) <= n else s[: n - 3] + "..."


def _item_line(it):
    what = it.get("label") or it["kind"]
    val = f"{it['value']} {it.get('unit') or ''}".strip() if it.get("value") is not None else \
        f"{it.get('media_type')} {it.get('bytes')} B sha256={str(it.get('sha256'))[:12]}..."
    return f"{it['id']}: {what} = {val} at {it.get('phenomenon_time')}"


# ------------------------------------------------------------------------------------------------------------- demo

def run(fast=False, out=OUT, usgs="auto"):
    """Run the demo. usgs: 'auto' (live if reachable), 'live', or 'recorded'. fast=True implies recorded. Returns
    {"ok": bool, "steps": [...], "text": transcript, "usgs_source": "live" | "recorded"}."""
    T = Transcript()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="irlpls-demo-"))
    saved = {"registry": irlpls._registry, "home": broker.HOME, "client": broker.PROTOCOL.get("client")}
    stack = contextlib.ExitStack()
    sim_s = 1.0 if fast else 6.0
    nap = (lambda s: time.sleep(min(s, 0.25))) if fast else time.sleep
    facts = {}
    try:
        broker.HOME = tmp / "home"                       # the broker's job files stay in this run's temp dir
        broker.PROTOCOL["client"] = dict(LOCAL, sleep=nap)

        # ------------------------------------------------------------------------------------------------ 1. setup
        T.step("Start two providers on localhost",
               "Both servers speak the same six operations over HTTP+JSON; nothing leaves this machine except, in live "
               "mode, the shim's own read-only calls to the public USGS API.")
        if fast or usgs == "recorded":
            live, why = False, "fast mode: no network" if fast else "recorded mode requested"
        else:
            live, why = usgs_reachable()
            if usgs == "live" and not live:
                raise SystemExit(f"--usgs live: {why}")
        facts["usgs_source"] = "live" if live else "recorded"
        if not live:
            stack.enter_context(recorded_usgs())
        sim = stack.enter_context(ProviderServer(SimFulfiller(seconds=sim_s), port=0, tick=0.1))
        T.out(f"simulated provider  irlpls.protocol.server + SimFulfiller({sim_s:g} s)  endpoint {sim.endpoint}")
        from cryptography.hazmat.primitives.asymmetric import ec
        shim_srv = S.serve_adapter("usgs-water", 0, block=False, signing_key=ec.generate_private_key(ec.SECP256R1()),
                                   kid="demo-shim-1", operator={"name": "irlpls protocol demo (localhost shim)"},
                                   **({} if live else {"media_fetcher": _recorded_media}))
        stack.callback(lambda: (shim_srv.shutdown(), shim_srv.server_close()))
        shim_base = f"http://127.0.0.1:{shim_srv.server_address[1]}"
        T.out(f"usgs-water shim     irlpls.protocol.shim.serve_adapter                 endpoint {shim_base}/irlpls")
        T.out(f"USGS data source:   {facts['usgs_source'].upper()} ({why})"
              + ("" if live else f"; recording taken {REC_AT} from the live API"))
        T.check(True, "both servers listening on 127.0.0.1")

        # -------------------------------------------------------------------------------------- 2. river, the router
        T.step(f"Router plans '{RIVER_NEED}'",
               "The broker ranks the registry's Capture Cards for the ask and picks a free live feed; it plans, it does not order.")
        job = irlpls.request(RIVER_NEED, **RIVER_HINTS)
        best = job.best()
        T.out(f"plan: {', '.join(o['card_id'] + ' (' + o['route'] + ', fit ' + str(o['fit']) + ')' for o in job.plan[:3])}")
        T.out(f"{len(job.ruled_out)} other providers ruled out with a reason each")
        T.check(best and best["card_id"] == "usgs-water" and best["route"] == "live", f"best: {best and best['card_id']} via {best and best['route']}")
        river_req = job._wire_request(exact=True)
        T.out(f"wire request (no policy, no why, no requester): {_j(river_req)}")

        T.step("Discover the usgs-water shim",
               "A strict client refuses a protocol endpoint that is not on the provider's own domain (draft section 3); "
               "the shim's card is unclaimed and names its operator, so it is used only as an explicitly accepted shim.")
        st, _, card = pc._send("GET", shim_base + "/.well-known/irlpls", headers={"Irlpls-Version": pc.VERSION}, **LOCAL)
        proto = card["protocol"]
        T.out(f"card {card['id']} status={card.get('status')} provider_url={card.get('provider_url')}")
        T.out(f"protocol: endpoint={proto['endpoint']} quote={proto['quote']} operator={proto['operator']['name']!r} (role {proto['operator']['role']})")
        try:
            pc.check_card(card, fetched_from=shim_base + "/.well-known/irlpls", allow_http=True)
            T.check(False, "strict discovery accepted a third-party endpoint")
        except pc.UnsafeEndpoint as e:
            T.check(True, f"strict discovery refuses it: {e.problem['detail']}")
        river = pc.Client(proto["endpoint"], card=card, sleep=nap, **LOCAL)
        T.check(card.get("status") == "unclaimed" and proto["operator"]["role"] == "shim",
                "accepted as an unclaimed card relayed by a disclosed shim operator")

        T.step("Quote the river reading",
               "Quotes cost nothing and dispatch nothing; a shim never invents a price: the offer is the card's (free) and not firm.")
        q = river.quote(river_req)
        offer = q["offers"][0]
        T.out(f"quote {q['id']} request_sha256={q['request_sha256'][:16]}... expires {q['expires_at']}")
        T.out(f"offer {offer['id']}: route={offer['route']} firm={offer['firm']} total={_j(offer.get('total'))} rights={_j(offer.get('rights'), 90)}")
        T.check(q["request_sha256"] == pc.request_sha256(river_req), "the quote is bound to the exact request sent (RFC 8785 hash)")

        T.step("Order the river reading",
               "A live order is delivered in the order response itself; the client generated the Idempotency-Key and a nonce.")
        r_nonce = pc.new_nonce()
        order = river.order({"quote_id": q["id"], "offer_id": offer["id"], "nonce": r_nonce})
        T.out(f"Idempotency-Key {river.last_idempotency_key}  nonce {r_nonce}")
        T.out(f"order {order['id']}: state={order['state']} total={_j(order.get('total'))} history={[h['state'] for h in order['status_history']]}")
        T.check(order["state"] == "delivered", "delivered at once (live route)")

        T.step("Deliver: readings with per-stage provenance",
               "Items arrive with ids, hashes and an 8-stage provenance whose levels come from the card ('basis: card'); the shim never raises a level.")
        d = river.delivery(order["id"])
        for it in d["items"]:
            T.out(_item_line(it))
        for s_ in d["provenance"]["stages"]:
            g = ", ".join(f"{k}={v['level']}/{v['basis']}" for k, v in (s_.get("guarantees") or {}).items())
            T.out(f"  stage {s_['stage']:<18} {s_['shown_as']:<24} {g}")
        T.out(f"relayed_by: {_j(d.get('relayed_by'))}")
        ucard = irlpls.card("usgs-water")
        raised = [(s_["stage"], k) for s_ in d["provenance"]["stages"] for k, v in (s_.get("guarantees") or {}).items()
                  if v.get("basis") == "card" and v["level"] != ucard["verification"][k]["level"]]
        T.check(d["complete"] and len(d["items"]) >= 1 and not raised,
                f"{len(d['items'])} item(s); every card-basis level equals the usgs-water card's")
        facts["river_items"] = len(d["items"])

        T.step("Verify the river receipts",
               "Each item carries a detached-JWS receipt signed by the shim operator (iss = the shim's origin), bound to our "
               "order, nonce, request hash and item hash. It attests custody only: USGS signed nothing.")
        st, _, jwks = pc._send("GET", proto["operator"]["jwks_uri"], headers={"Irlpls-Version": pc.VERSION}, **LOCAL)
        T.out(f"operator keys from {proto['operator']['jwks_uri']}: {[k['kid'] for k in jwks['keys']]}")
        results = [pc.verify_receipt(it, jwks, iss=shim_base, order_id=order["id"], nonce=r_nonce,
                                     request_sha256=order["request_sha256"]) for it in d["items"]]
        for res in results:
            T.out(f"{res['item_id']}: verified={res['verified']} ({res['verifier']}) {res['reason']}")
        T.out(f"signer says: {d['items'][0]['receipt']['signer']['attests'][:110]}...")
        T.check(all(r_["verified"] is True for r_ in results), "every receipt verifies with the operator's key")

        # ---------------------------------------------------------------------------------- 3. storefront photo (sim)
        T.step(f"Router plans '{SHOP_NEED}'",
               "With the simulator's served card in the registry, the router ranks it first for a storefront photo and "
               "records that it speaks the protocol (sdk.protocol = its endpoint).")
        sim_card = pc.discover(sim.provider_url, **LOCAL)
        cards = tmp / "cards"
        shutil.copytree(ROOT / "cards", cards)
        (cards / f"{sim_card['id']}.json").write_text(json.dumps(sim_card))
        irlpls._registry = irlpls.Registry(source=str(cards), include_samples=True)
        shop = irlpls.request(SHOP_NEED, **SHOP_HINTS)
        sb = shop.best()
        T.out(f"plan: {', '.join(o['card_id'] + ' (fit ' + str(o['fit']) + ')' for o in shop.plan[:4])}")
        T.check(sb and sb["card_id"] == "irlpls-sim" and sb["sdk"]["protocol"] == sim.endpoint,
                f"best: {sb and sb['card_id']} at {sb and sb['sdk']['protocol']}")
        shop_req = shop._wire_request(exact=True)
        T.out(f"wire request: {_j(shop_req)}")

        T.step("Discover the simulator",
               "GET /.well-known/irlpls returns the card with its protocol block; the client checks endpoint and key URL are the provider's own origin.")
        c = pc.Client.from_card(sim_card, fetched_from=sim.provider_url + "/.well-known/irlpls", sleep=nap, **LOCAL)
        sp = sim_card["protocol"]
        T.out(f"card {sim_card['id']} ({sim_card['status']}): endpoint={sp['endpoint']} jwks_uri={sp['jwks_uri']} cancel={sp['cancel']}")
        T.check(True, "discovery checks pass (same origin)")

        T.step("Quote the storefront photo",
               "A firm, priced offer under the buyer's ceiling (max_total 40 USD), with its rights and proof levels stated up front.")
        sq = c.quote(shop_req, {"max_total": pc.money(40)})
        so = sq["offers"][0]
        T.out(f"quote {sq['id']} expires {sq['expires_at']}")
        T.out(f"offer {so['id']}: route={so['route']} firm={so['firm']} total={_j(so['total'])} deliver_by={so.get('deliver_by')}")
        T.out(f"  rights={_j(so.get('rights'), 120)}")
        T.out(f"  proof={_j(so.get('proof'), 140)}")
        T.check(so["firm"] and pc.amount(so["total"]) <= 40, "firm offer within max_total")

        T.step("Order with an Idempotency-Key",
               "POST /orders carries a client-generated Idempotency-Key and a fresh nonce; the provider accepts (202) and points to the order.")
        key, s_nonce = pc.new_key(), pc.new_nonce()
        body = {"quote_id": sq["id"], "offer_id": so["id"], "nonce": s_nonce, "client_reference": shop.id}
        so1 = c.order(body, idempotency_key=key)
        T.out(f"Idempotency-Key {key}  nonce {s_nonce}")
        T.out(f"order {so1['id']}: state={so1['state']} total={_j(so1['total'])} Retry-After={c.last_retry_after}s")
        T.check(so1["state"] in ("submitted", "in_flight"), "accepted, not yet delivered")

        T.step("Replay the same order",
               "A retry with the same key and body returns the stored response: the same order, never a second one. "
               "The same key with a different body is refused.")
        so2 = c.order(body, idempotency_key=key)
        T.out(f"replay -> order {so2['id']}")
        with sim.lock:
            n_orders = len(sim.store.orders)
        T.check(so2["id"] == so1["id"] and n_orders == 1, f"same order id; the provider holds {n_orders} order")
        try:
            c.order(dict(body, nonce=pc.new_nonce()), idempotency_key=key)
            T.check(False, "same key + different body was accepted")
        except pc.ProtocolError as e:
            T.check(e.code == "idempotency_key_reused", f"same key, different body -> {e.status} {e.code}")

        T.step("Poll status until delivered",
               "Status follows the Job's states (submitted -> in_flight -> delivered) with the provider's own words in "
               "provider_status; the client waits as long as Retry-After says.")
        seen, cur, deadline = [], so1, time.monotonic() + sim_s * 6 + 10
        while True:
            mark = (cur["state"], cur.get("provider_status"), cur["progress"]["delivered"])
            if mark not in seen:
                seen.append(mark)
                T.out(f"status: {cur['state']:<10} provider_status={cur.get('provider_status')!s:<26} delivered {cur['progress']['delivered']}/{cur['progress']['ordered']}")
            if cur["state"] in ("delivered", "failed") or time.monotonic() > deadline:
                break
            nap(c.last_retry_after or 1)
            cur = c.status(so1["id"])
        T.out(f"history: {[h['state'] + ('/' + h['provider_status'] if h.get('provider_status') else '') for h in cur['status_history']]}")
        T.check(cur["state"] == "delivered", "delivered")

        T.step("Deliver the photo",
               "The item comes with a short-lived media URL and its sha256; the client downloads it and checks the hash.")
        sd = c.delivery(so1["id"])
        photo = sd["items"][0]
        T.out(_item_line(photo))
        media = c.fetch_media(photo)
        T.check(len(media) == photo["bytes"], f"downloaded {len(media)} B; sha256 matches the item")

        T.step("Verify the photo receipt",
               "The receipt is signed by the provider (iss = its origin) with the key from its own jwks_uri, and binds this "
               "order, our nonce, the quoted request hash and the media hash.")
        checks = c.verify_delivery(sd, nonce=s_nonce, request_sha256=sq["request_sha256"])
        p = photo["receipt"]["payload"]
        kid = json.loads(pc.b64u_dec(photo["receipt"]["jws"].split(".")[0]))["kid"]
        T.out(f"receipt kid={kid} iss={p['iss']} order_id={p['order_id']} nonce={p['nonce']}")
        T.out(f"  media.sha256={p['media']['sha256'][:16]}... request_sha256={p['request_sha256'][:16]}...")
        T.out(f"{checks[0]['item_id']}: verified={checks[0]['verified']} ({checks[0]['verifier']}) {checks[0]['reason']}")
        T.check(checks[0]["verified"] is True, "receipt verifies")

        T.step("Tamper with the receipt",
               "Changing any signed field, or presenting the receipt for someone else's order, fails verification.")
        bad = copy.deepcopy(sd)
        bad["items"][0]["receipt"]["payload"]["lat"] = 40.0
        r1 = c.verify_delivery(bad, nonce=s_nonce, request_sha256=sq["request_sha256"])[0]
        T.out(f"lat changed {sd['items'][0]['receipt']['payload'].get('lat')} -> 40.0: verified={r1['verified']} ({r1['reason']})")
        bad2 = copy.deepcopy(sd)
        bad2["items"][0]["receipt"]["payload"]["media"]["sha256"] = "0" * 64
        r2 = c.verify_delivery(bad2, nonce=s_nonce, request_sha256=sq["request_sha256"])[0]
        T.out(f"media hash swapped:          verified={r2['verified']} ({r2['reason']})")
        r3 = c.verify_delivery(sd, nonce=pc.new_nonce(), request_sha256=sq["request_sha256"])[0]
        T.out(f"checked against another nonce: verified={r3['verified']} ({r3['reason']})")
        T.check(r1["verified"] is False and r2["verified"] is False and r3["verified"] is False, "all three fail")

        T.step(f"Refuse a person-targeting ask: '{PERSON_NEED}'",
               "Asks aimed at a specific person are refused at every layer: the router plans nothing, the provider "
               "refuses a request flagged targets_person, and the shim refuses on the text alone.")
        pj = irlpls.request(PERSON_NEED, **SHOP_HINTS)
        T.out(f"router: targets_person={pj.request.targets_person}; plan={[o['card_id'] for o in pj.plan]}; "
              f"{sum(1 for o in pj.ruled_out if 'policy:allow_person_targeting' in o['reasons'])} people-based providers ruled out for it")
        T.check(pj.request.targets_person and not pj.plan, "router: nothing planned, nothing sent")
        preq = pj._wire_request(exact=True)
        try:
            c.quote(preq)
            T.check(False, "provider quoted a person-targeting request")
        except pc.ProtocolError as e:
            T.check(e.code == "person_targeting_refused", f"provider (sim) -> {e.status} {e.code}")
        try:
            river.quote({"need": PERSON_NEED, "lat": 29.9683, "lon": -98.9054, "country_code": "US"})
            T.check(False, "shim quoted a person-targeting request")
        except pc.ProtocolError as e:
            T.check(e.code == "person_targeting_refused", f"shim (usgs-water, no flag sent) -> {e.status} {e.code}")
    finally:
        stack.close()
        irlpls._registry, broker.HOME = saved["registry"], saved["home"]
        broker.PROTOCOL["client"] = saved["client"]
        shutil.rmtree(tmp, ignore_errors=True)

    ok = all(s_["ok"] for s_ in T.steps)
    T.out("")
    T.out(f"RESULT: {'all ' + str(len(T.steps)) + ' steps passed' if ok else 'FAILED: ' + ', '.join(str(s_['n']) for s_ in T.steps if not s_['ok'])}", stamp=False)
    result = {"ok": ok, "steps": T.steps, "text": T.text, "usgs_source": facts.get("usgs_source"), "fast": fast,
              "started": T.started.strftime("%Y-%m-%dT%H:%M:%SZ")}
    if out:
        pathlib.Path(out).write_text(markdown(result))
    return result


def markdown(r):
    src = r["usgs_source"]
    lines = [
        "# irlpls protocol: end-to-end demo",
        "",
        f"Run {r['started']} with `tools/protocol_demo.py`{' --fast' if r['fast'] else ''}. Private: everything ran on "
        "127.0.0.1 and nothing was published or ordered from a real provider.",
        "",
        f"- **USGS data: {src.upper()}.** "
        + ("The shim called the public USGS API during the run." if src == "live" else
           f"The live API was not used; the shim answered from a recording of the live API taken {REC_AT}."),
        "- **Simulated provider:** `irlpls.protocol.server.ProviderServer` + `SimFulfiller`. Its photo is a generated "
        "gradient PNG. It never captures anything.",
        "- **Client and router:** `irlpls.protocol.client` (discover, quote, order, status, delivery, receipts) and the "
        "`irlpls` broker (`irlpls.request` plans; it never orders).",
        "",
        "Rerun: `uv run --with cryptography --with jsonschema python tools/protocol_demo.py` (add `--fast` for no network). "
        "Smoke test: `tests/test_protocol_demo.py`.",
        "",
        "## What each step proves",
        "",
        "| # | Step | Result | What it proves |",
        "|---|------|--------|----------------|",
    ]
    for s_ in r["steps"]:
        lines.append(f"| {s_['n']} | {s_['title'].replace('|', '/')} | {'pass' if s_['ok'] else 'FAIL'} | {s_['proves'].replace('|', '/')} |")
    lines += [
        "",
        "## Transcript",
        "",
        "Printed by the run as it happened. `[+s]` is seconds since start; each step header carries its UTC time.",
        "",
        "```text",
        r["text"].strip("\n"),
        "```",
        "",
        "## Limits",
        "",
        "- The river receipts are the shim operator's, not USGS's. They prove custody (the shim relayed these bytes), not "
        "that the reading is true. The guarantee levels stay the card's.",
        "- The strict client refuses the shim's card because its endpoint is not on usgs.gov. The demo uses it as an "
        "accepted unclaimed card with a disclosed operator (draft section 3). It does not bypass the check.",
        "- The reference server (simulator) refuses person targeting only when the request carries `targets_person`; it "
        "does not read the text itself. The router sets the flag and the shim checks the text.",
        "- Plain HTTP on localhost, by explicit opt-out (`allow_http`, `allow_private`). Real endpoints must be HTTPS on public hosts.",
        "",
    ]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fast", action="store_true", help="no network: recorded USGS data, 1 s simulator")
    ap.add_argument("--usgs", choices=("auto", "live", "recorded"), default="auto")
    ap.add_argument("--out", default=str(OUT), help="markdown output (default docs/protocol/DEMO.md)")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    r = run(fast=a.fast, out=None if a.no_write else a.out, usgs=a.usgs)
    if not a.no_write:
        print(f"\nwrote {a.out}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
