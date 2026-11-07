"""One tolerant ISO 8601 / RFC 3339 parser with identical results on Python 3.10 and 3.11+ (stdlib only).

`datetime.fromisoformat` on 3.10 rejects what 3.11+ accepts: a trailing Z, fractional seconds that are not 3 or 6
digits (".5Z", nanoseconds), and the compact basic form (20261106T100000Z). `parse` normalises those first.
"""
import re
from datetime import datetime

_RX = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2}|\d{8})"
    r"(?:[Tt ](?P<time>\d{2}(?::?\d{2}(?::?\d{2}(?:[.,]\d+)?)?)?)"
    r"\s*(?P<tz>[Zz]|[+-]\d{2}(?::?\d{2}(?::?\d{2})?)?)?)?"
)


def _canon(s):
    m = _RX.fullmatch(s)
    if not m:
        return None
    d = m["date"]
    if len(d) == 8:
        d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    out = d
    t = m["time"]
    if t:
        frac = ""
        t = t.replace(",", ".")
        if "." in t:
            t, frac = t.split(".", 1)
            frac = "." + frac[:6].ljust(6, "0")
        digits = t.replace(":", "")
        hh, mm, ss = digits[:2], digits[2:4] or "00", digits[4:6] or "00"
        out += f"T{hh}:{mm}:{ss}{frac}"
        tz = m["tz"]
        if tz:
            if tz in ("Z", "z"):
                tz = "+00:00"
            else:
                z = tz[0] + tz[1:].replace(":", "")
                tz = z[:3] + ":" + (z[3:5] or "00") + (":" + z[5:7] if len(z) > 5 else "")
            out += tz
    return out


def parse(s):
    """An ISO 8601 date-time as a datetime (aware when it carries Z or an offset, else naive), or None. Never raises.

    Only the forms in `_RX` are read (no ISO week/ordinal dates), so 3.10 and 3.11+ agree. A bare date gives midnight (naive), as `datetime.fromisoformat` does on 3.11+. Non-strings give None."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    if not s:
        return None
    try:
        c = _canon(s)
        return datetime.fromisoformat(c) if c is not None else None
    except Exception:
        return None
