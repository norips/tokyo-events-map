"""Tokyo Weekender (tokyoweekender.com) adapter.

Events are a WordPress custom post type exposed on the public REST API
(/wp-json/wp/v2/events) with structured ACF fields: dates, times, price,
venue and a Google Maps embed that carries the coordinates.
"""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.error
from typing import Iterator

from ..http import Fetcher
from ..models import Event, normalize_categories
from . import register
from .base import EventSource, ScrapeContext

log = logging.getLogger("tokyo_events.tokyoweekender")
API = "https://www.tokyoweekender.com/wp-json/wp/v2"
FIELDS = "id,link,title,acf,event-categories,yoast_head_json.og_image,yoast_head_json.og_description"
LAT_RANGE, LNG_RANGE = (35.1, 36.2), (138.9, 140.4)


def _date(value: str | None) -> str | None:
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", value or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _hhmm(value: str | None) -> str | None:
    m = re.match(r"(\d{2}):(\d{2})", value or "")
    return f"{m.group(1)}:{m.group(2)}" if m else None


def _12h(hhmm: str) -> str:
    h, m = map(int, hhmm.split(":"))
    return f"{(h % 12) or 12}:{m:02d}{'am' if h < 12 else 'pm'}"


def build_event(item: dict, category_slugs: dict[int, str]) -> Event | None:
    acf = item.get("acf") or {}
    if acf.get("online"):
        return None  # this is a map of places
    start = _date(acf.get("start_date"))
    if not start:
        return None
    end = _date(acf.get("end_date")) or start
    if end < start:
        end = start

    lat = lng = None
    m = re.search(r"!2d(-?\d+\.\d+)!3d(-?\d+\.\d+)", acf.get("google_map_embed_map") or "")
    if m:
        lng, lat = float(m.group(1)), float(m.group(2))
        if not (LAT_RANGE[0] < lat < LAT_RANGE[1] and LNG_RANGE[0] < lng < LNG_RANGE[1]):
            return None  # outside Greater Tokyo

    start_time, end_time = _hhmm(acf.get("start_time")), _hhmm(acf.get("end_time"))
    time_text = " – ".join(_12h(t) for t in (start_time, end_time) if t) or None
    extra = (acf.get("time_extra_info") or "").strip().strip("()").strip()
    # Skip notes that only restate the hours, e.g. "10 a.m.-9 p.m."
    if extra and not re.fullmatch(r"[\d:\s.apm\-–~]+", extra.lower()):
        time_text = f"{time_text} · {extra}" if time_text else extra

    price_from = acf.get("price_from")
    price_text = "Free" if acf.get("free") else (acf.get("price") or None)

    yoast = item.get("yoast_head_json") or {}
    images = yoast.get("og_image") or []
    title = html.unescape((item.get("title") or {}).get("rendered") or "").strip()
    raw_categories = [category_slugs[c] for c in item.get("event-categories") or [] if c in category_slugs]

    return Event(
        source=TokyoWeekender.name,
        source_id=str(item["id"]),
        url=item["link"],
        title=title,
        start_date=start,
        end_date=end,
        summary=html.unescape(yoast.get("og_description") or ""),
        image=images[0].get("url") if images else None,
        start_time=start_time,
        end_time=end_time,
        time_text=time_text,
        categories=normalize_categories(raw_categories, title),
        source_categories=raw_categories,
        price_text=price_text,
        price_min=0.0 if acf.get("free") else (float(price_from) if isinstance(price_from, (int, float)) else None),
        venue_name=html.unescape(acf.get("venue_name") or "") or None,
        venue_address=acf.get("venue_location") or None,
        lat=lat,
        lng=lng,
        geo_precision="venue" if lat is not None else None,
    )


@register
class TokyoWeekender(EventSource):
    name = "tokyoweekender"
    label = "Tokyo Weekender"
    short = "TW"
    color = "#e11d48"
    homepage = "https://www.tokyoweekender.com/events/"
    priority = 20  # structured hours and prices, but no neighbourhood or station

    def __init__(self, fetcher: Fetcher | None = None):
        self.fetcher = fetcher or Fetcher(min_interval=1.5)

    def scrape(self, ctx: ScrapeContext) -> Iterator[Event]:
        cats = json.loads(self.fetcher.get(f"{API}/event-categories?per_page=100&_fields=id,slug"))
        slugs = {c["id"]: c["slug"] for c in cats}
        past_pages = 0
        for page in range(1, ctx.max_pages + 1):
            url = f"{API}/events?per_page=100&page={page}&orderby=date&order=desc&_fields={FIELDS}"
            try:
                items = json.loads(self.fetcher.get(url))
            except urllib.error.HTTPError as e:
                if e.code == 400:  # WordPress: page number past the end
                    break
                raise
            if not items:
                break
            ends = []
            for item in items:
                try:
                    event = build_event(item, slugs)
                except Exception:
                    log.exception("skipping %s", item.get("link"))
                    continue
                if not event:
                    continue
                ends.append(event.end_date)
                if event.end_date >= ctx.today and event.start_date <= ctx.horizon:
                    yield event
            # Posts are newest first; after two pages of only past events, older
            # pages hold nothing current.
            past_pages = past_pages + 1 if ends and max(ends) < ctx.today else 0
            if past_pages >= 2:
                break
