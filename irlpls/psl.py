"""Public Suffix List lookups, from the vendored snapshot in irlpls/data/public_suffix_list.dat (MPL-2.0).

- `public_suffix(host)`: the public suffix of a host name ("co.uk" for "a.b.co.uk"), per the PSL algorithm
  (https://github.com/publicsuffix/list/wiki/Format): exception rules win, else the longest matching rule, else "*".
- `registrable_domain(host)`: the public suffix plus one label ("b.co.uk"); None for an IP address, an empty host, or
  a host that is itself a public suffix ("co.uk", "github.io"), which has no registrable domain.

Both the ICANN and PRIVATE sections are used: "alice.github.io" and "bob.github.io" are different sites, which is
what a same-site check needs. `same_owner(host, domain)` is the stricter card-ownership rule: the serving host is the
declared host itself or its "www." twin, on one registrable domain. Host labels and rules compare in their ASCII (punycode) form, lower case. Stdlib only.
"""
import ipaddress
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "public_suffix_list.dat"


def _ascii(label):
    if label.isascii():
        return label.lower()
    try:
        return label.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return label.lower()


def parse(text):
    """(rules, wildcards, exceptions) as sets of dotted ASCII names. A wildcard "*.ck" is stored as "ck"."""
    rules, wild, exc = set(), set(), set()
    for line in text.splitlines():
        line = line.strip().split()[0] if line.strip() else ""
        if not line or line.startswith("//"):
            continue
        if line.startswith("!"):
            exc.add(".".join(_ascii(x) for x in line[1:].split(".")))
        elif line.startswith("*."):
            wild.add(".".join(_ascii(x) for x in line[2:].split(".")))
        else:
            rules.add(".".join(_ascii(x) for x in line.split(".")))
    return frozenset(rules), frozenset(wild), frozenset(exc)


@lru_cache(maxsize=1)
def _list():
    return parse(DATA.read_text(encoding="utf-8"))


def _labels(host):
    host = (host or "").strip().rstrip(".")
    if not host or host.startswith("[") or "." not in host and ":" in host:
        return None
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    labels = [_ascii(x) for x in host.split(".")]
    return labels if all(labels) else None


def _suffix_len(labels, lists=None):
    rules, wild, exc = lists or _list()
    best = 1  # the implicit "*" rule
    for i in range(len(labels)):
        name = ".".join(labels[i:])
        n = len(labels) - i
        if name in exc:
            return n - 1  # an exception rule prevails; its suffix drops the leftmost label
        if name in rules:
            best = max(best, n)
        if i > 0 and ".".join(labels[i:]) in wild:  # "*.<name>" matches labels[i-1:]
            best = max(best, n + 1)
    return best


def public_suffix(host, lists=None):
    labels = _labels(host)
    if labels is None:
        return None
    return ".".join(labels[-_suffix_len(labels, lists):])


def registrable_domain(host, lists=None):
    labels = _labels(host)
    if labels is None:
        return None
    n = _suffix_len(labels, lists)
    return ".".join(labels[-(n + 1):]) if len(labels) > n else None


def same_site(a, b):
    """True when two host names are equal or share a registrable domain. IP addresses and single-label hosts such as
    localhost match only themselves; two different hosts under a bare public suffix never match; empty never matches."""
    a, b = (a or "").lower().rstrip("."), (b or "").lower().rstrip(".")
    if not a or not b:
        return False
    if a == b:
        return True
    try:
        return ipaddress.ip_address(a) == ipaddress.ip_address(b)
    except ValueError:
        pass
    ra = registrable_domain(a)
    return ra is not None and ra == registrable_domain(b)


def _host(h):
    labels = _labels(h)
    return ".".join(labels) if labels else (h or "").strip().rstrip(".").lower()


def same_owner(host, domain):
    """True when a card fetched from `host` may speak for the declared `domain`: the two are the same host, or differ
    only by a leading "www." and share a registrable domain under the PSL (PRIVATE suffixes such as github.io count).
    A subdomain ("sub.example.com" for "example.com"), a lookalike ("evil-example.com"), a suffix trick
    ("example.com.evil.net") or a neighbour under a shared suffix ("bob.github.io" for "alice.github.io") is refused.
    Both sides are compared lower case in ASCII (punycode) form, trailing dot dropped; empty never matches."""
    h, d = _host(host), _host(domain)
    if not h or not d:
        return False
    if h == d:
        return True
    if h != "www." + d and d != "www." + h:
        return False
    r = registrable_domain(h)
    return r is not None and r == registrable_domain(d)
