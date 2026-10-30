"""Load Capture Cards. Order: explicit path/URL, $IRLPLS_CARDS, the repo's cards/ folder, then the bundled snapshot."""
import json
import os
import pathlib

from . import http

ASSURANCES = [
    ("human", "Enrolled person"),
    ("application", "Device or app attestation"),
    ("camera", "Signed capture"),
    ("time", "Time-stamp"),
    ("location", "Authenticated place"),
    ("content", "Content integrity"),
    ("corroboration", "Corroboration"),
]
_HERE = pathlib.Path(__file__).parent


def _from(source):
    if source.startswith(("http://", "https://")):
        data = http.get(source)
    else:
        p = pathlib.Path(source)
        if p.is_dir():
            return [json.loads(f.read_text()) for f in sorted(p.glob("*.json"))]
        data = json.loads(p.read_text())
    return data["cards"] if isinstance(data, dict) else data


class Registry:
    def __init__(self, source=None, include_samples=False):
        source = source or os.environ.get("IRLPLS_CARDS")
        if not source:
            repo_cards = _HERE.parent / "cards"
            source = str(repo_cards if repo_cards.is_dir() else _HERE / "data" / "cards.json")
        cards = _from(source)
        self.all = cards
        from .match import in_scope
        self.cards = [c for c in cards if in_scope(c) and (include_samples or c.get("status") != "sample")]
        self._by_id = {c["id"]: c for c in cards}

    def get(self, card_id):
        return self._by_id.get(card_id)

    def __iter__(self):
        return iter(self.cards)

    def __len__(self):
        return len(self.cards)
