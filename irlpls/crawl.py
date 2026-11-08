"""Lane A crawler: fetch https://<domain>/.well-known/irlpls.json through irlpls.safefetch only, and decide.

The file is either one card or an index {spec_version, cards: [{id, url, sha256}]}. Rules, in order:
 - every request goes through safefetch.Fetcher (SSRF checks, size cap, robots, redirect re-validation);
 - at most one request per 10 s per host (default Fetcher is built with that limit); Retry-After on 429/503 is
   stored in the returned state as `retry_after_until` and honoured on the next call;
 - If-None-Match is sent from the passed-in state; 304 gives status "unchanged";
 - index entries are fetched from the same registrable domain, and sha256(raw bytes) must equal the entry's sha256;
 - domain control: the card's provider_url registrable domain must equal the fetched host's registrable domain
   (Public Suffix List via irlpls.psl, so shared suffixes such as github.io do not leak ownership);
 - each card then goes through irlpls.gate.gate_card.
No file is written here; the caller decides what to do with accepted cards (the CLI quarantines them).
"""
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from . import gate, psl, safefetch

WELL_KNOWN = "/.well-known/irlpls.json"
HOST_INTERVAL = 10.0
MAX_INDEX_CARDS = 200
MAX_RETRY_AFTER = 86400


@dataclass
class CardOutcome:
    id: str
    status: str  # accepted | rejected
    sha256: str = None
    url: str = None
    errors: list = field(default_factory=list)
    card: dict = None


@dataclass
class CrawlResult:
    domain: str
    status: str  # accepted | rejected | unchanged | error
    cards: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    state: dict = field(default_factory=dict)
    http_status: int = None
    retry_after: float = None

    @property
    def accepted(self):
        return [c for c in self.cards if c.status == "accepted"]


def make_fetcher():
    return safefetch.Fetcher(limiter=safefetch.TokenBucket(1.0 / HOST_INTERVAL, 1.0))


def _err(code, msg, ptr=""):
    return {"code": code, "pointer": ptr, "message": msg}


def _retry_after(h, now):
    v = (h.get("retry-after") or "").strip()
    if v.isdigit():
        return min(float(v), MAX_RETRY_AFTER)
    return None


def _norm_domain(domain):
    d = (domain or "").strip().lower().rstrip(".")
    if not re.fullmatch(r"[a-z0-9.-]+", d) or "." not in d:
        return None
    return d


def _owns(fetched_host, card):
    url = card.get("provider_url") if isinstance(card, dict) else None
    h = urlsplit(url).hostname if isinstance(url, str) else None
    a, b = psl.registrable_domain(h or ""), psl.registrable_domain(fetched_host)
    return a is not None and a == b


def _get(fetcher, url):
    return fetcher.fetch(url, content_types=["application/json"], max_bytes=gate.MAX_BYTES * 8)


def crawl_domain(domain, fetcher=None, state=None, now=None):
    """Crawl one domain. `state` is the dict returned by the previous crawl (or None)."""
    fetcher = fetcher or make_fetcher()
    now = time.time() if now is None else now
    st = dict(state or {})
    d = _norm_domain(domain)
    if not d:
        return CrawlResult(str(domain), "error", errors=[_err("C_DOMAIN", "not a plain domain name")], state=st)
    if st.get("retry_after_until", 0) > now:
        return CrawlResult(d, "error", errors=[_err("C_BACKOFF", "Retry-After not yet elapsed")], state=st,
                           retry_after=st["retry_after_until"] - now)
    url = f"https://{d}{WELL_KNOWN}"
    try:
        r = _fetch_cond(fetcher, url, st.get("etag"))
    except safefetch.SafeFetchError as e:
        return CrawlResult(d, "error", errors=[_err("C_FETCH_" + type(e).__name__.upper(), str(e))], state=st)
    res = CrawlResult(d, "error", state=st, http_status=r.status)
    if r.status == 304:
        res.status = "unchanged"
        return res
    if r.status in (429, 503):
        ra = _retry_after(r.headers, now)
        if ra is not None:
            st["retry_after_until"] = now + ra
            res.retry_after = ra
        res.errors.append(_err("C_HTTP_" + str(r.status), "server asked us to back off"))
        return res
    if r.status != 200:
        res.errors.append(_err("C_HTTP_" + str(r.status), f"unexpected status {r.status}"))
        return res
    st.pop("retry_after_until", None)
    final_host = urlsplit(r.url).hostname or d
    if psl.registrable_domain(final_host) != psl.registrable_domain(d):
        res.errors.append(_err("C_REDIRECT_DOMAIN", f"redirected to another registrable domain ({final_host})"))
        return res
    try:
        doc = json.loads(r.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        res.errors.append(_err("C_JSON", "well-known file is not UTF-8 JSON"))
        return res
    body_sha = hashlib.sha256(r.body).hexdigest()
    if r.headers.get("etag"):
        st["etag"] = r.headers["etag"]
    st["sha256"] = body_sha
    if isinstance(doc, dict) and "cards" in doc:
        _index(res, doc, d, fetcher, final_host)
    else:
        res.cards.append(_judge(doc, r.body, final_host, body_sha, url))
    res.status = "accepted" if res.accepted else "rejected"
    return res


def _fetch_cond(fetcher, url, etag):
    """Conditional GET: the validator goes to the serving host only (safefetch drops it on a cross-host redirect)."""
    if etag:
        return fetcher.fetch(url, content_types=["application/json"], max_bytes=gate.MAX_BYTES * 8,
                             headers={"If-None-Match": etag})
    return _get(fetcher, url)


def _judge(card, raw, host, sha, url, expect_sha=None):
    cid = card.get("id") if isinstance(card, dict) and isinstance(card.get("id"), str) else "(unknown)"
    out = CardOutcome(cid, "rejected", sha, url)
    if expect_sha is not None and sha != expect_sha.lower():
        out.errors.append(_err("C_SHA256", "sha256 of the fetched bytes does not match the index entry"))
        return out
    if isinstance(card, dict) and not _owns(host, card):
        out.errors.append(_err("C_DOMAIN_CONTROL", "provider_url registrable domain differs from the serving domain",
                               "/provider_url"))
        return out
    g = gate.gate_card(card, raw_size=len(raw))
    if not g.ok:
        out.errors = list(g.errors)
        return out
    out.status, out.card = "accepted", card
    return out


def _index(res, doc, d, fetcher, host):
    entries = doc.get("cards")
    if not isinstance(entries, list) or not entries:
        res.errors.append(_err("C_INDEX", "cards must be a non-empty list", "/cards"))
        return
    if len(entries) > MAX_INDEX_CARDS:
        res.errors.append(_err("C_INDEX_LARGE", f"more than {MAX_INDEX_CARDS} cards in one index", "/cards"))
        return
    known = res.state.setdefault("cards", {})
    for i, e in enumerate(entries):
        ptr = f"/cards/{i}"
        ok = (isinstance(e, dict) and isinstance(e.get("id"), str) and isinstance(e.get("url"), str)
              and isinstance(e.get("sha256"), str) and re.fullmatch(r"[0-9a-fA-F]{64}", e["sha256"]))
        if not ok:
            res.cards.append(CardOutcome("(unknown)", "rejected", errors=[_err("C_INDEX_ENTRY", "entry needs id, url, 64-hex sha256", ptr)]))
            continue
        h = urlsplit(e["url"]).hostname or ""
        if psl.registrable_domain(h) != psl.registrable_domain(d):
            res.cards.append(CardOutcome(e["id"], "rejected", url=e["url"], errors=[_err("C_DOMAIN_CONTROL", "card url is on another registrable domain", ptr + "/url")]))
            continue
        try:
            r = _get(fetcher, e["url"])
        except safefetch.SafeFetchError as ex:
            res.cards.append(CardOutcome(e["id"], "rejected", url=e["url"], errors=[_err("C_FETCH_" + type(ex).__name__.upper(), str(ex), ptr + "/url")]))
            continue
        fh = urlsplit(r.url).hostname or h
        if r.status != 200 or psl.registrable_domain(fh) != psl.registrable_domain(d):
            res.cards.append(CardOutcome(e["id"], "rejected", url=e["url"], errors=[_err("C_HTTP_" + str(r.status), "card not served (or moved off-domain)", ptr + "/url")]))
            continue
        sha = hashlib.sha256(r.body).hexdigest()
        try:
            card = json.loads(r.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            res.cards.append(CardOutcome(e["id"], "rejected", sha, e["url"], [_err("C_JSON", "card is not UTF-8 JSON", ptr + "/url")]))
            continue
        o = _judge(card, r.body, fh, sha, e["url"], expect_sha=e["sha256"])
        if isinstance(card, dict) and card.get("id") != e["id"] and o.status == "accepted":
            o.status, o.card = "rejected", None
            o.errors.append(_err("C_ID_MISMATCH", "card id differs from the index entry", ptr + "/id"))
        o.id = e["id"]
        if o.status == "accepted":
            known[e["id"]] = sha
        res.cards.append(o)
