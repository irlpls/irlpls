"""irlpls wire protocol (draft 2026-10-26; docs/protocol/PROTOCOL-DRAFT.md, schemas in schema/protocol/).

Reference provider:  python -m irlpls.protocol.server --port 8811 --sim
Python:              from irlpls.protocol import ProviderServer, SimFulfiller, verify_receipt

Names are imported lazily so other protocol modules (client, shims) can live in this package without import cycles,
and so `cryptography` is needed only when signing or verifying.
"""
import importlib

VERSION = "2026-10-26"

_EXPORTS = {
    "ProviderServer": "server", "Fulfiller": "server", "SimFulfiller": "server", "Terms": "server",
    "Observation": "server", "Update": "server",
    "Problem": "models", "PROBLEMS": "models",
    "jcs": "receipts", "request_sha256": "receipts", "ReceiptSigner": "receipts", "WebhookSigner": "receipts",
    "verify_receipt": "receipts", "verify_webhook": "receipts", "ReceiptError": "receipts",
}

__all__ = ["VERSION", *_EXPORTS]


def __getattr__(name):
    mod = _EXPORTS.get(name)
    if mod is None:
        raise AttributeError(f"module 'irlpls.protocol' has no attribute {name!r}")
    return getattr(importlib.import_module(f"{__name__}.{mod}"), name)
