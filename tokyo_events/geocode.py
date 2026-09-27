"""Approximate geocoding via OpenStreetMap Nominatim, cached in the store.

Used only as a fallback for events whose source gives a neighbourhood or a
venue name but no coordinates. Nominatim's policy: max 1 request/second and an identifying UA.
"""

from __future__ import annotations

import json
import logging
import urllib.parse

from .http import Fetcher
from .store import EventStore

log = logging.getLogger("tokyo_events.geocode")
NOMINATIM = "https://nominatim.openstreetmap.org/search"
# Rough bounding box of Greater Tokyo (Kanto): west, north, east, south
VIEWBOX = "138.9,36.3,140.3,35.1"


class AreaGeocoder:
    def __init__(self, store: EventStore):
        self.store = store
        self.fetcher = Fetcher(min_interval=1.1)

    def lookup(self, place: str) -> tuple[float, float] | None:
        query = f"{place}, Japan"
        cached = self.store.geocode_lookup(query)
        if cached is not None:
            return cached if cached[0] is not None else None
        params = urllib.parse.urlencode(
            {"q": query, "format": "jsonv2", "limit": 1, "viewbox": VIEWBOX, "bounded": 1}
        )
        try:
            results = json.loads(self.fetcher.get(f"{NOMINATIM}?{params}"))
        except Exception as exc:  # network trouble: don't cache, try again next run
            log.warning("geocoding %r failed: %r", place, exc)
            return None
        point = (float(results[0]["lat"]), float(results[0]["lon"])) if results else None
        self.store.geocode_save(query, *(point or (None, None)))
        return point
