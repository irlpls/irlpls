"""Open Cosmos DataCosmos: price and buy imagery your tasking produced, by API. Contract only: client credentials come
from the DataCosmos team. Set OPEN_COSMOS_CLIENT_ID and OPEN_COSMOS_CLIENT_SECRET; OPEN_COSMOS_CONTRACT_ID and
OPEN_COSMOS_ORGANISATION_ID (or the spec) name the contract and organisation.

What is and is not by API. New captures are requested in the DataCosmos tasking tool; whether a tasking request can be
created by API is unverified (the developer centre's Tasking API page is empty and the OpenAPI answers 401). What is
documented is pricing and buying a catalogue item: once a tasked capture is in the catalogue, the price call quotes it
for your contract and the order call buys it from the organisation's prepaid USD credits. So commission() needs the
STAC collection and item id of that capture, and never tasks a satellite itself.

Sources (read 2026-10-28):
- Client credentials: POST https://login.open-cosmos.com/oauth/token, audience https://beeapp.open-cosmos.com
  https://app.open-cosmos.com/help/developer-center/authentication
- Price: GET https://app.open-cosmos.com/api/data/v0/order/price?collection&item&contract_id -> {"data": {value,
  discount, final, currency}} (deprecated in favour of GET /api/data/v1/dpap/policies/{policy_id}/catalog/price, whose
  query parameters are not listed, so the documented v0 call is used)
  https://app.open-cosmos.com/help/developer-center/datacosmos/api/ordering/pricing
- Order: POST /api/data/v0/order/orders {type: IMAGE, data: {order_line_items: [{collection, item, level}]},
  organisation, contract_id}; paid at once from credits ("status": "PAID") or left "UNPAID"
  https://app.open-cosmos.com/help/developer-center/datacosmos/api/ordering/purchasing
Fixtures in tests/fixtures/adapters/open-cosmos-datacosmos/ are the documented examples (fixture: documented-shape)."""
import urllib.parse
from decimal import Decimal

from .. import http
from . import _a9
from ._oauth import TokenCache
from .base import Adapter, MissingCredentials, observation

BASE = "https://app.open-cosmos.com/api/data"
TOKEN_URL = "https://login.open-cosmos.com/oauth/token"
AUDIENCE = "https://beeapp.open-cosmos.com"
DOCS = "https://app.open-cosmos.com/help/developer-center/datacosmos/api/ordering/pricing"
GET_KEY = "https://app.open-cosmos.com/help/developer-center/authentication"
TOKEN_NOTES = (f"Token: POST {TOKEN_URL} with grant_type=client_credentials, client_id=OPEN_COSMOS_CLIENT_ID, "
               f"client_secret=OPEN_COSMOS_CLIENT_SECRET, audience={AUDIENCE}; use access_token as the bearer token.")
TASKING_NOTE = ("New captures are requested in the DataCosmos tasking tool (tasking by API is unverified); this prices "
                "the resulting catalogue item for your contract.")
CONTRACT = _a9.ph("OPEN_COSMOS_CONTRACT_ID", "your DataCosmos contract id (integer)", "DataCosmos > Organisation > Contracts")
ITEM = [_a9.ph("OPEN_COSMOS_COLLECTION", "the STAC collection of your tasked capture", "DataCosmos catalogue (STAC)"),
        _a9.ph("OPEN_COSMOS_ITEM", "the STAC item id of your tasked capture", "DataCosmos catalogue (STAC)")]


class OpenCosmosDatacosmos(Adapter):
    card_id = "open-cosmos-datacosmos"
    env = "OPEN_COSMOS_CLIENT_ID"
    how_to_get = ("Client credentials are issued by the DataCosmos team on a contract; set OPEN_COSMOS_CLIENT_ID, "
                  "OPEN_COSMOS_CLIENT_SECRET, OPEN_COSMOS_CONTRACT_ID and OPEN_COSMOS_ORGANISATION_ID.")
    can_commission = True
    _tokens = TokenCache()

    def configured(self):
        return all(_a9.env(n) for n in ("OPEN_COSMOS_CLIENT_ID", "OPEN_COSMOS_CLIENT_SECRET"))

    def _h(self):
        secret = _a9.env("OPEN_COSMOS_CLIENT_SECRET")
        if not secret:
            raise MissingCredentials(f"open-cosmos-datacosmos: set OPEN_COSMOS_CLIENT_SECRET too. {self.how_to_get}")
        return {"Authorization": "Bearer " + self._tokens.client_credentials(TOKEN_URL, self.key(), secret, audience=AUDIENCE)}

    @staticmethod
    def _price_url(collection, item, contract):
        q = "collection=" + collection + "&item=" + item + "&contract_id=" + str(contract)
        return f"{BASE}/v0/order/price?{q}"

    def prepare(self, req, route, operation=None, order_id=None):
        """Default: the catalogue price for a tasked capture (GET; buys nothing). "order" buys it from credits;
        "status" reads an order. Collection and item are placeholders: the capture is tasked in the web tool."""
        if route != "commission":
            return None
        op = operation or "quote"
        common = dict(token_name="OPEN_COSMOS_ACCESS_TOKEN", token_notes=TOKEN_NOTES, get_key_url=GET_KEY, docs_url=DOCS)
        if op == "quote":
            return _a9.oauth_call(self.card_id, "quote", "GET",
                                  self._price_url("{OPEN_COSMOS_COLLECTION}", "{OPEN_COSMOS_ITEM}", "{OPEN_COSMOS_CONTRACT_ID}"),
                                  placeholders=[CONTRACT] + ITEM, notes=TASKING_NOTE + " Returns {data: {value, discount, "
                                  "final, currency}} (USD). Successor: GET /api/data/v1/dpap/policies/{contract}/catalog/price.",
                                  **common)
        if op == "order":
            body = {"type": "IMAGE", "data": {"order_line_items": [{"collection": "{OPEN_COSMOS_COLLECTION}",
                                                                    "item": "{OPEN_COSMOS_ITEM}", "level": "{OPEN_COSMOS_LEVEL}"}]},
                    "organisation": "{OPEN_COSMOS_ORGANISATION_ID}", "contract_id": "{OPEN_COSMOS_CONTRACT_ID}"}
            pl = [CONTRACT] + ITEM + [_a9.ph("OPEN_COSMOS_LEVEL", "processing level, e.g. L2A", "the STAC item"),
                                      _a9.ph("OPEN_COSMOS_ORGANISATION_ID", "your organisation's numeric id", "DataCosmos > Organisation")]
            return _a9.oauth_call(self.card_id, "order", "POST", f"{BASE}/v0/order/orders", body=body, placeholders=pl,
                                  notes="Pays at once from the organisation's credits when they cover it (status PAID); "
                                        "otherwise the order stays UNPAID. Numbers go in as integers.", **common)
        if op == "status":
            oid = order_id or "{OPEN_COSMOS_ORDER_ID}"
            pl = None if order_id else [_a9.ph("OPEN_COSMOS_ORDER_ID", "the order id", "data.id of the order response")]
            return _a9.oauth_call(self.card_id, "status", "GET", f"{BASE}/v0/order/orders/{oid}", placeholders=pl,
                                  notes="data.status PAID or UNPAID; order_line_items[].delivered says what has arrived.",
                                  **common)
        return None

    def _ids(self, spec):
        contract = spec.get("contract_id") or _a9.env("OPEN_COSMOS_CONTRACT_ID")
        org = spec.get("organisation") or _a9.env("OPEN_COSMOS_ORGANISATION_ID")
        return contract, org

    def commission(self, spec, confirm=False):
        """Buy a tasked capture from the catalogue. spec: collection, item, level, contract_id and organisation (or the
        env vars). Without confirm: with credentials set, fetches the contract price (a catalogue price, kept as the
        provider sent it, never a firm offer) and buys nothing."""
        contract, org = self._ids(spec)
        need = (("collection", spec.get("collection")), ("item", spec.get("item")), ("level", spec.get("level")),
                ("contract_id", contract), ("organisation", org))
        missing = [k for k, v in need if not v]
        if missing:
            return _a9.dry("POST", f"{BASE}/v0/order/orders", None, missing=missing, note=TASKING_NOTE + " Give the "
                           "missing values; nothing was sent.")
        body = {"type": "IMAGE", "data": {"order_line_items": [{"collection": spec["collection"], "item": spec["item"],
                                                                 "level": spec["level"]}]},
                "organisation": int(org), "contract_id": int(contract)}
        if confirm:
            return _a9.call(self.card_id, http.post, f"{BASE}/v0/order/orders", body=body, headers=self._h(), creds=self.how_to_get)
        out = _a9.dry("POST", f"{BASE}/v0/order/orders", body)
        if self.configured() and spec.get("quote") is not False:
            url = self._price_url(urllib.parse.quote(spec["collection"], safe=""), urllib.parse.quote(spec["item"], safe=""), int(contract))
            try:
                d = (http.get(url, headers=self._h()) or {}).get("data") or {}
                out["catalogue_price"] = {k: d.get(k) for k in ("value", "discount", "final", "currency")}
            except http.HTTPError as e:
                out["warnings"] = [_a9.http_warning(self.card_id, e, "a catalogue price")]
        return out

    def status(self, order):
        oid = ((order or {}).get("data") or {}).get("id") or order.get("id")
        return _a9.call(self.card_id, http.get, f"{BASE}/v0/order/orders/{oid}", headers=self._h(), creds=self.how_to_get)

    status_path = ("data", "status")
    # Only PAID and UNPAID are documented. Neither says the imagery has arrived (line items carry `delivered`), so both
    # stay in_flight; an UNPAID order waits for credits.
    status_map = {"paid": "in_flight", "unpaid": "in_flight"}

    def order_total(self, resp):
        """USD the order says it charges: the sum of its line items' final prices, when all are in USD."""
        items = ((resp or {}).get("data") or {}).get("order_line_items") or []
        total = Decimal(0)
        for li in items:
            p = li.get("price") or {}
            v = _a9.usd(p.get("final"))
            if p.get("currency") != "USD" or v is None:
                return None
            total += v
        return total if items else None

    def deliverables(self, resp):
        return [observation(self.card_id, "optical-product", None, None, None, label=f"{li.get('collection')}/{li.get('item')}",
                            item=li.get("item"), collection=li.get("collection"), level=li.get("level"))
                for li in (((resp or {}).get("data") or {}).get("order_line_items") or []) if li.get("delivered") is True]
