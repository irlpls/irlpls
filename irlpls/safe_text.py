"""Make provider- or card-derived text safe to print, put in a plain-text file or turn into a link.

Card text is written by third parties. Printed raw it can carry ANSI escape sequences (a terminal can be told to
rewrite the screen), a newline that forges a line or section in llms.txt, or a javascript: URL. Stdlib only.
"""
import re

# CSI / OSC / other ESC sequences, then any remaining C0, DEL, C1 controls and the Unicode line/paragraph separators.
_ESC = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-Z\\-_]?|\x9b[0-?]*[ -/]*[@-~]")
_CTL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_CTL_KEEP_NL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f  ]")
_BIDI = re.compile(r"[‪-‮⁦-⁩‎‏]")
_URL_BAD = re.compile(r"[\s\x00-\x1f\x7f-\x9f  <>\"'`\\]")


def strip_controls(text, multiline=False, limit=None):
    """text with ANSI sequences and control characters removed. Single line by default (each control, newline
    included, becomes one space, runs collapsed); multiline=True keeps newlines and tabs. limit truncates."""
    s = _BIDI.sub("", _ESC.sub("", "" if text is None else str(text)))
    if multiline:
        s = _CTL_KEEP_NL.sub("", s.replace("\r\n", "\n").replace("\r", "\n"))
    else:
        s = re.sub(r" {2,}", " ", _CTL.sub(" ", s)).strip()
    return s[:limit] if limit else s


def is_safe_url(url, allow_http=False):
    """True for a plain https URL (http too with allow_http): no whitespace, controls, quotes or angle brackets."""
    if not isinstance(url, str) or not url or len(url) > 2000 or _URL_BAD.search(url):
        return False
    low = url.lower()
    return low.startswith("https://") or (allow_http and low.startswith("http://"))


def safe_href(url):
    """The URL when it is safe to put in an href (http or https), else None: render it as text then."""
    return url if is_safe_url(url, allow_http=True) else None


URL_KEYS = {"url", "provider_url", "docs_url", "openapi_url", "resource_metadata", "source_url", "evidence_url"}


def sanitize_card(card):
    """A copy of a card with control characters stripped from every string and every URL field dropped unless it is
    a safe http(s) URL. Defence in depth for the build: the schema already refuses these, a hostile card must not
    rely on the validator having run."""
    def walk(o, key=None):
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                if k in URL_KEYS and isinstance(v, str) and not safe_href(v):
                    continue
                out[strip_controls(k) if isinstance(k, str) else k] = walk(v, k)
            return out
        if isinstance(o, list):
            return [walk(v, key) for v in o]
        return strip_controls(o) if isinstance(o, str) else o
    return walk(card)
