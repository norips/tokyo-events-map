"""Tokyo Art Beat (tokyoartbeat.com) adapter: exhibitions and art events.

The English event listing is a Next.js page whose embedded __NEXT_DATA__
already holds every open event (up to 1,000) with venue coordinates, so one
request covers the whole source. It spans Japan; we keep Greater Tokyo.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Iterator

from ..http import Fetcher
from ..models import Event, normalize_categories
from . import register
from .base import EventSource, ScrapeContext

log = logging.getLogger("tokyo_events.tokyoartbeat")
LISTING = "https://www.tokyoartbeat.com/en/events"
EVENT_URL = "https://www.tokyoartbeat.com/en/events/-/{slug}"
# Greater Tokyo (Tokyo, Kanagawa, Saitama, Chiba) bounding box
LAT_RANGE, LNG_RANGE = (35.1, 36.2), (138.9, 140.4)
DAY_ABBR = {"Sunday": "Sun", "Monday": "Mon", "Tuesday": "Tue", "Wednesday": "Wed",
            "Thursday": "Thu", "Friday": "Fri", "Saturday": "Sat", "Holidays": "holidays"}


def _fields(obj) -> dict:
    return (obj or {}).get("fields") or {}


def listing_events(page_html: str) -> list[dict]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page_html, re.S)
    if not m:
        raise ValueError("Tokyo Art Beat page has no __NEXT_DATA__")
    fallback = json.loads(m.group(1))["props"]["pageProps"].get("fallback", {})
    for key, value in fallback.items():
        if '"EventSearch"' in key and isinstance(value, dict):
            return value.get("data") or []
    raise ValueError("Tokyo Art Beat page has no EventSearch data")


def build_event(raw: dict) -> Event | None:
    if raw.get("permanentShow"):
        return None  # permanent collections are not events
    venue = _fields(raw.get("venue"))
    geo = venue.get("geoInfo") or {}
    lat, lng = geo.get("lat"), geo.get("lon")
    if lat is None or lng is None:
        return None
    if not (LAT_RANGE[0] < lat < LAT_RANGE[1] and LNG_RANGE[0] < lng < LNG_RANGE[1]):
        return None
    start = raw.get("scheduleStartsOn")
    end = raw.get("scheduleEndsOn") or start
    if not start or end >= "2090":
        return None

    image = _fields(_fields(raw.get("imageposter")).get("file")) or _fields(raw.get("imageposter")).get("file") or {}
    image_url = image.get("url")
    if image_url:
        image_url = ("https:" + image_url if image_url.startswith("//") else image_url) + "?w=640&fm=jpg"

    raw_categories = [_fields(c).get("name") for c in raw.get("categories") or [] if _fields(c).get("name")]
    categories = set(normalize_categories(raw_categories)) - {"other"}
    categories.add("art")  # everything on Tokyo Art Beat is art-world programming

    closed = raw.get("closedDays") if not raw.get("hideClosedDays") else None
    closed = closed or venue.get("closedDays")
    area = _fields(venue.get("localArea")).get("name")

    return Event(
        source=TokyoArtBeat.name,
        source_id=raw["id"],
        url=EVENT_URL.format(slug=raw["slug"]),
        title=raw.get("eventName") or raw["slug"],
        start_date=start,
        end_date=end,
        image=image_url,
        time_text=("Closed " + ", ".join(DAY_ABBR.get(d, d) for d in closed)) if closed else None,
        date_label="End date not fixed" if raw.get("scheduleEndDateUnfix") else None,
        categories=sorted(categories),
        source_categories=raw_categories,
        venue_name=venue.get("fullName"),
        area=None if not area or area.startswith("Tokyo:") else area,
        lat=float(lat),
        lng=float(lng),
        geo_precision="venue",
    )


@register
class TokyoArtBeat(EventSource):
    name = "tokyoartbeat"
    label = "Tokyo Art Beat"
    short = "AB"
    color = "#0f172a"
    homepage = "https://www.tokyoartbeat.com/en/events"
    priority = 30  # exact venue coordinates, but no prices, hours or descriptions

    def __init__(self, fetcher: Fetcher | None = None):
        self.fetcher = fetcher or Fetcher(min_interval=1.5)

    def scrape(self, ctx: ScrapeContext) -> Iterator[Event]:
        for raw in listing_events(self.fetcher.get(LISTING)):
            try:
                event = build_event(raw)
            except Exception:
                log.exception("skipping %s", raw.get("slug"))
                continue
            if event and event.end_date >= ctx.today and event.start_date <= ctx.horizon:
                yield event
