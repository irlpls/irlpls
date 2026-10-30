"""Policies: the rules your organisation or agent sets for acquiring real-world data, executed by the SDK.

A Policy is checked against each provider's Capture Card when matching (find flags violations) and again before
any commission is sent (commission refuses to place an order that violates it). It works only from what cards
state: an unknown licence right or an unproven guarantee counts as a violation, never as a pass.
"""
import math
from dataclasses import asdict, dataclass, field
from decimal import Decimal

LEVELS = {"none": 0, "claimed": 1, "evidenced": 2}


def valid_money(x):
    """A real, finite, non-negative number (int, float or Decimal). NaN, inf, negatives, booleans and strings are never money."""
    if type(x) is Decimal:
        return x.is_finite() and x >= 0
    return type(x) in (int, float) and math.isfinite(x) and x >= 0


def as_decimal(x):
    """Money as Decimal without a trip through binary float: a Decimal as is, an int or float by its shortest repr
    (12.1 -> Decimal("12.1"), never 12.0999...). None unless valid_money(x)."""
    if not valid_money(x):
        return None
    return x if type(x) is Decimal else Decimal(repr(x))


def _shown(v):
    """A Decimal policy amount as a JSON number for display (to_dict); enforcement always uses the Decimal itself."""
    return (int(v) if v == v.to_integral_value() else float(v)) if type(v) is Decimal and v.is_finite() else v


def is_free(card):
    """True if using the provider costs nothing: access.cost is 'free' or 'free with key'. Freemium does not count."""
    return (card.get("access") or {}).get("cost") in ("free", "free with key")


class PolicyViolation(RuntimeError):
    def __init__(self, card_id, violations):
        super().__init__(f"{card_id} violates policy: " + "; ".join(violations))
        self.card_id, self.violations = card_id, violations


@dataclass
class Policy:
    require: dict = field(default_factory=dict)   # guarantee -> minimum level, e.g. {"location": "evidenced", "time": "claimed"}
    max_unit_price_usd: float = None              # ceiling on the card's stated starting price
    max_order_usd: float = None                   # ceiling on an order's total; 0 = free providers only (checked at plan time too)
    need_ai_training: bool = False                # card must state AI training is allowed
    need_resale: bool = False                     # card must state resale is allowed
    allow: list = None                            # only these provider ids
    deny: list = field(default_factory=list)      # never these provider ids
    include_unclaimed: bool = True                # allow cards the provider hasn't confirmed
    free_only: bool = False                       # only providers whose card says they are free to use
    auto_submit: bool = False                     # standing mandate: the broker may confirm orders within max_order_usd itself
    require_c2pa: bool = False                    # the camera guarantee must be backed by C2PA (standard: "c2pa")
    min_assurance_level: int = None               # ... from a product at this C2PA assurance level or higher
    allow_person_targeting: bool = False          # a request that targets a specific person may go to people-based providers

    def __post_init__(self):
        from .registry import ASSURANCES
        for k in ("max_unit_price_usd", "max_order_usd"):
            v = getattr(self, k)
            # NaN, inf and negatives are accepted here and fail closed in violations(); strings and bools are not numbers
            if v is not None and type(v) not in (int, float, Decimal):
                raise ValueError(f"policy {k} must be a number, not {v!r}")
        for k in ("need_ai_training", "need_resale", "include_unclaimed", "free_only", "auto_submit", "require_c2pa", "allow_person_targeting"):
            if not isinstance(getattr(self, k), bool):
                raise ValueError(f"policy {k} must be true or false, not {getattr(self, k)!r}")
        for k in ("allow", "deny"):
            v = getattr(self, k)
            if v is not None and not (isinstance(v, (list, tuple, set)) and all(isinstance(i, str) for i in v)):
                raise ValueError(f"policy {k} must be a list of provider ids")
        v = self.min_assurance_level
        if v is not None and type(v) not in (int, float):
            raise ValueError(f"policy min_assurance_level must be a number, not {v!r}")
        if not isinstance(self.require, dict):
            raise ValueError("policy require must be an object mapping guarantee to level")
        keys = {k for k, _ in ASSURANCES}
        for k, lvl in (self.require or {}).items():
            if k not in keys:
                raise ValueError(f"policy require: unknown guarantee {k!r}; use one of {', '.join(sorted(keys))}")
            if lvl not in ("claimed", "evidenced"):
                raise ValueError(f"policy require: {k} must be 'claimed' or 'evidenced', not {lvl!r}")

    @classmethod
    def from_dict(cls, d):
        """A policy from a dict (JSON, an MCP argument). A misspelt rule is an error, never dropped: a spending ceiling
        that silently disappears ("max_order" for max_order_usd) would let every order through."""
        import difflib
        if d is not None and not isinstance(d, dict):
            raise ValueError(f"policy must be an object, not {type(d).__name__}")
        fields = [k for k in cls.__dataclass_fields__]
        for k in d or {}:
            if k not in cls.__dataclass_fields__:
                near = difflib.get_close_matches(str(k), fields, n=1, cutoff=0.5)
                raise ValueError(f"unknown policy field {k!r}" + (f" (did you mean {near[0]}?)" if near else "")
                                 + f"; fields: {', '.join(fields)}")
        p = cls(**dict(d or {}))
        p.check_values()
        return p

    def check_values(self):
        """Ceilings a person sets must be real amounts: 0 or more and finite (0 means free only). Raises ValueError.
        (A Policy built directly with a bad value is still accepted and fails closed in violations().)"""
        for k in ("max_unit_price_usd", "max_order_usd"):
            v = getattr(self, k)
            if v is not None and not valid_money(v):
                raise ValueError(f"policy {k} must be a number, 0 or more (got {v!r}); 0 means free providers only")
        return self

    def to_dict(self):
        """The policy as set. 0 is a value (max_order_usd: 0 means free only), so only None, False and empty
        containers are left out; include_unclaimed is always shown, and allow is shown whenever it is set."""
        return {k: _shown(v) for k, v in asdict(self).items()
                if k in ("include_unclaimed",) or (k == "allow" and v is not None) or not _unset(v)}

    def check(self, card):
        """Violations of this policy by a Capture Card (empty list = compliant)."""
        return [text for _, text in self.violations(card)]

    def violations(self, card):
        """Violations as (code, text): code is "policy:<rule>" (the Policy field that rules the provider out).
        Everything that can be judged from the card is judged here, at plan time; only a commission's quoted total
        waits for the quote."""
        v = []
        cid = card["id"]
        if self.allow is not None and cid not in self.allow:
            v.append(("policy:allow", "not on the allow list"))
        if cid in (self.deny or []):
            v.append(("policy:deny", "on the deny list"))
        if not self.include_unclaimed and card.get("status") != "provider-maintained":
            v.append(("policy:include_unclaimed", "card not confirmed by the provider"))
        for k, lvl in self.require.items():
            have = card["verification"][k]["level"]
            if LEVELS[have] < LEVELS[lvl]:
                v.append(("policy:require", f"{k} must be {lvl}, card says {have}"))
        p = (card.get("pricing") or {}).get("from_usd")
        p_ok = valid_money(p)
        if self.max_unit_price_usd is not None:
            if not valid_money(self.max_unit_price_usd):
                v.append(("policy:max_unit_price_usd", f"invalid max_unit_price_usd {self.max_unit_price_usd!r}"))
            elif not p_ok or not p:
                v.append(("policy:max_unit_price_usd", "price not published" if p is None or p == 0 else f"invalid price {p!r}"))
            elif not (p <= self.max_unit_price_usd):
                v.append(("policy:max_unit_price_usd", f"starts at ${p:g}, over ${self.max_unit_price_usd:g}"))
        # An order can't cost less than the card's starting price, and a provider that isn't free can't be used for $0.
        if self.max_order_usd is not None and not is_free(card):
            if not valid_money(self.max_order_usd) or self.max_order_usd <= 0:
                v.append(("policy:max_order_usd", "costs money; max_order_usd is " + (f"${self.max_order_usd:g}" if valid_money(self.max_order_usd) else repr(self.max_order_usd))))
            elif p is not None and p != 0 and not p_ok:
                v.append(("policy:max_order_usd", f"invalid price {p!r}"))
            elif not p and not card.get("protocol"):
                # AGENTS 7: an unpublished price checked against a ceiling is a violation, never a pass. (A provider that
                # speaks the protocol quotes a firm total, which the broker re-checks against the ceiling before ordering.)
                v.append(("policy:max_order_usd", f"price not published, so max_order_usd ${self.max_order_usd:g} can't be checked; ask the provider for a quote"))
            elif p and not (p <= self.max_order_usd):
                v.append(("policy:max_order_usd", f"starts at ${p:g}, over max_order_usd ${self.max_order_usd:g}"))
        if self.free_only and not is_free(card):
            v.append(("policy:free_only", "not free to use"))
        lic = card.get("licensing") or {}
        if self.need_ai_training and lic.get("ai_training") != "allowed":
            v.append(("policy:need_ai_training", "AI training rights " + ("forbidden" if lic.get("ai_training") == "forbidden" else "not stated")))
        if self.need_resale and lic.get("resale") != "allowed":
            v.append(("policy:need_resale", "resale rights " + ("forbidden" if lic.get("resale") == "forbidden" else "not stated")))
        cam = card["verification"]["camera"]
        if self.require_c2pa and cam.get("standard") != "c2pa":
            v.append(("policy:require_c2pa", "no C2PA-signed capture"))
        if self.min_assurance_level and (cam.get("standard") != "c2pa" or (cam.get("assurance_level") or 0) < self.min_assurance_level):
            v.append(("policy:min_assurance_level", f"C2PA assurance level {cam.get('assurance_level') or 'none'}, need {self.min_assurance_level}"))
        return v


def _unset(v):
    """None, False or an empty list/dict. Not 0: a zero ceiling is a real setting."""
    return v is None or v is False or (isinstance(v, (list, dict)) and not v)
