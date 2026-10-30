"""Provider adapters, keyed by Capture Card id.

Adding a provider touches no shared file: drop one module in this package (module name = card id with "-" → "_",
e.g. usgs_earthquakes.py) holding exactly one Adapter subclass with a `card_id`. It is found at import time, after the
adapters listed below, in module-name order. Modules starting with "_" (shared helpers such as _oauth.py) are skipped.
"""
import importlib
import pkgutil

from .base import Adapter, MissingCredentials, NotSupported
from .beemaps import BeeMaps
from .caltrans_cctv import CaltransCCTV
from .noaa_tides import NOAATides
from .nws import NWS
from .openaq import OpenAQ
from .planet import Planet
from .prolific import Prolific
from .proxypics import ProxyPics
from .purpleair import PurpleAir
from .rentahuman import RentAHuman
from .skyfi import SkyFi
from .spexi import Spexi
from .umbra import Umbra
from .usgs_water import USGSWater
from .windy_webcams import WindyWebcams
from .uk_ea_flood import UKEAFlood
from .pegelonline import Pegelonline
from .sensor_community import SensorCommunity
from .copernicus import Copernicus
from .fintraffic import Fintraffic
from .alertcalifornia import ALERTCalifornia

_LISTED = (NWS(), USGSWater(), NOAATides(), CaltransCCTV(), WindyWebcams(), OpenAQ(), PurpleAir(), BeeMaps(), ProxyPics(),
           RentAHuman(), Spexi(), SkyFi(), Planet(), Umbra(), Prolific(), UKEAFlood(), Pegelonline(), SensorCommunity(),
           Copernicus(), Fintraffic(), ALERTCalifornia())


def discover(package, path, skip=()):
    """One adapter instance per module in `package` (at `path`), in module-name order. A module must define exactly one
    Adapter subclass with a card_id (catalog.adapters() reads one card_id per file); anything else fails loudly."""
    found = []
    for m in sorted(pkgutil.iter_modules(path), key=lambda m: m.name):
        if m.name.startswith("_") or m.name == "base" or m.name in skip:
            continue
        mod = importlib.import_module(f"{package}.{m.name}")
        cls = [o for o in vars(mod).values() if isinstance(o, type) and issubclass(o, Adapter) and o is not Adapter
               and o.__module__ == mod.__name__ and o.card_id]
        if len(cls) != 1:
            raise ImportError(f"{mod.__name__}: expected exactly one Adapter subclass with a card_id, found {len(cls)}")
        found.append(cls[0]())
    return found


def _registry(adapters):
    out = {}
    for a in adapters:
        if a.card_id in out:
            raise ImportError(f"two adapters claim card {a.card_id!r}: {type(out[a.card_id]).__module__} and {type(a).__module__}")
        out[a.card_id] = a
    return out


ADAPTERS = _registry(_LISTED + tuple(discover(__name__, __path__, skip={type(a).__module__.rsplit(".", 1)[1] for a in _LISTED})))


def get(card_id):
    return ADAPTERS.get(card_id)


def configured():
    """Adapters you can call right now (no key needed, or key present in the environment)."""
    return [cid for cid, a in ADAPTERS.items() if a.configured()]
