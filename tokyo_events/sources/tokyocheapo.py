"""Tokyo Cheapo (tokyocheapo.com/events) adapter.

Listing pages give the card data (title, categories, area, price, hours).
Detail pages add what the map needs: schema.org Event JSON-LD for exact
dates and the embedded map component for coordinates.
"""

from __future__ import annotations

import calendar
import hashlib
import html
import json
import logging
import re
from typing import Iterator

from ..http import Blocked, Fetcher, NotFound
from ..models import Event, normalize_categories
from . import register
from .base import EventSource, ScrapeContext, Unchanged

log = logging.getLogger("tokyo_events.tokyocheapo")
BASE = "https://tokyocheapo.com"
CARD_SPLIT = '<article class="article card card--event'


def _text(fragment: str) -> str:
    fragment = re.sub(r"<(svg|script|style)\b.*?</\1>", "", fragment, flags=re.S)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(fragment)).strip()


def _month_range(year: int, month: int, qualifier: str | None, is_end: bool) -> str:
    """Resolve "Early/Mid/Late <month>" into a concrete day."""
    last = calendar.monthrange(year, month)[1]
    spans = {"early": (1, 10), "mid": (11, 20), "late": (21, last)}
    lo, hi = spans.get((qualifier or "").lower(), (1, last))
    return f"{year:04d}-{month:02d}-{hi if is_end else lo:02d}"


def _resolve_date(value: str, qualifier: str | None, is_end: bool) -> tuple[str, bool] | None:
    """JSON-LD date -> (YYYY-MM-DD, approximate?)."""
    if not value:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        return m.group(0), False
    m = re.match(r"(\d{4})-(\d{2})$", value)
    if m:
        return _month_range(int(m.group(1)), int(m.group(2)), qualifier, is_end), True
    m = re.match(r"(\d{4})$", value)
    if m:
        return (f"{m.group(1)}-12-31" if is_end else f"{m.group(1)}-01-01"), True
    return None


def _time(value: str) -> str | None:
    m = re.search(r"T(\d{2}:\d{2})", value or "")
    return m.group(1) if m else None


def parse_card(card: str) -> dict | None:
    href = re.search(r'class="card__image"[^>]*href="([^"]+)"', card) or re.search(r'href="(/events/[^"]+)"', card)
    if not href:
        return None
    path = href.group(1)
    slug = path.rstrip("/").split("/")[-1]
    title = re.search(r'<h3 class="card__title">(.*?)</h3>', card, re.S)
    img = re.search(r'<img[^>]+src="([^"]+)"', card)
    excerpt = re.search(r'<p class="card__excerpt">(.*?)</p>', card, re.S)
    hours = re.search(r'title="Start/end time">.*?</div>\s*<span>(.*?)</span>', card, re.S)
    price = re.search(r'title="Entry">.*?</div>(.*?)</div>', card, re.S)
    area = re.search(r'class="location"[^>]*>(.*?)</a>', card, re.S)
    date_box = re.search(r'card--event__date-box[^"]*">(.*?)</div>\s*</div>\s*</div>', card, re.S)
    date_text = _text(date_box.group(1)) if date_box else ""
    excerpt_text = _text(re.sub(r'<a[^>]*class="read-more".*?</a>', "", excerpt.group(1), flags=re.S)) if excerpt else ""
    return {
        "slug": slug,
        "url": BASE + path if path.startswith("/") else path,
        "title": _text(title.group(1)) if title else slug,
        "image": html.unescape(img.group(1)) if img else None,
        "excerpt": excerpt_text.rstrip(" …").strip(),
        "time_text": _text(hours.group(1)) if hours else None,
        "price_text": _text(price.group(1)) if price else None,
        "categories": re.findall(r'/event-category/([^"/]+)"', card),
        "area": _text(area.group(1)) if area else None,
        "date_text": date_text,
        "qualifiers": re.findall(r"\b(Early|Mid|Late)\b", date_text),
        "fingerprint": hashlib.sha1(re.sub(r"\s+", " ", card).encode()).hexdigest(),
    }


def parse_listing(page_html: str) -> list[dict]:
    cards = []
    for chunk in page_html.split(CARD_SPLIT)[1:]:
        chunk = chunk.split("</article>")[0]
        card = parse_card(chunk)
        if card:
            cards.append(card)
    return cards


def parse_detail(page_html: str) -> dict:
    out: dict = {}
    for m in re.finditer(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page_html, re.S):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Event":
            out["ld"] = data
            break
    m = re.search(r'component-name="apple-maps">\s*<script type="application/json">(.*?)</script>', page_html, re.S)
    if m:
        try:
            out["map"] = json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r'<meta property="og:image" content="([^"]+)"', page_html)
    if m:
        out["og_image"] = html.unescape(m.group(1))
    m = re.search(r'<span class="station-distance-string">(.*?)</span>', page_html, re.S)
    if m:
        out["station"] = _text(m.group(1))
    return out


def build_event(card: dict, detail: dict) -> Event | None:
    ld = detail.get("ld") or {}
    q = card["qualifiers"]
    start = _resolve_date(ld.get("startDate", ""), q[0] if q else None, is_end=False)
    end = _resolve_date(ld.get("endDate", "") or ld.get("startDate", ""), q[-1] if q else None, is_end=True)
    if not start:
        return None
    end = end or start
    if end[0] < start[0]:
        end = (start[0], end[1])

    lat = lng = None
    venue_name = venue_address = None
    mp = detail.get("map") or {}
    try:
        lat, lng = float(mp["lat"]), float(mp["lng"])
        if not (20 < lat < 50 and 120 < lng < 155):  # guard against junk coords
            lat = lng = None
    except (KeyError, TypeError, ValueError):
        pass
    venue_name = html.unescape(mp.get("title") or "") or None
    venue_address = html.unescape(mp.get("dispaddr") or "") or None
    locations = ld.get("location") or []
    if isinstance(locations, dict):
        locations = [locations]
    if locations:
        first = locations[0]
        addr = first.get("address") or {}
        venue_name = venue_name or html.unescape(first.get("name") or "") or None
        venue_address = venue_address or addr.get("streetAddress")
        card["area"] = card["area"] or addr.get("addressLocality")

    offers = ld.get("offers") or {}
    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    approx = start[1] or end[1]
    summary = html.unescape(ld.get("description") or "") or card["excerpt"]

    return Event(
        source=TokyoCheapo.name,
        source_id=card["slug"],
        url=card["url"],
        title=html.unescape(ld.get("name") or card["title"]),
        summary=summary,
        image=card["image"] or detail.get("og_image"),
        start_date=start[0],
        end_date=end[0],
        start_time=None if approx else _time(ld.get("startDate", "")),
        end_time=None if approx else _time(ld.get("endDate", "")),
        time_text=card["time_text"],
        date_approx=approx,
        date_label=card["date_text"] if approx else None,
        categories=normalize_categories(card["categories"], card["title"]),
        source_categories=card["categories"],
        price_text=card["price_text"],
        price_min=_num(offers.get("lowPrice")),
        price_max=_num(offers.get("highPrice")),
        venue_name=venue_name,
        venue_address=venue_address,
        area=card["area"],
        station=detail.get("station"),
        lat=lat,
        lng=lng,
        geo_precision="venue" if lat is not None else None,
        fingerprint=card["fingerprint"],
    )


@register
class TokyoCheapo(EventSource):
    name = "tokyocheapo"
    label = "Tokyo Cheapo"
    short = "TC"
    color = "#16a34a"
    homepage = "https://tokyocheapo.com/events/"
    priority = 10  # richest records: area, nearest station, hours, price

    def __init__(self, fetcher: Fetcher | None = None):
        # The site sits behind a bot firewall: stay sequential and slow.
        self.fetcher = fetcher or Fetcher(min_interval=1.5)

    def _detail(self, card: dict) -> Event | None:
        try:
            return build_event(card, parse_detail(self.fetcher.get(card["url"])))
        except Blocked:
            raise
        except NotFound:
            return None
        except Exception:
            log.exception("skipping %s", card["url"])
            return None

    def scrape(self, ctx: ScrapeContext) -> Iterator[Event | Unchanged]:
        seen: set[str] = set()
        for page_no in range(1, ctx.max_pages + 1):
            url = f"{BASE}/events/" if page_no == 1 else f"{BASE}/events/page/{page_no}/"
            try:
                cards = parse_listing(self.fetcher.get(url))
            except NotFound:
                break
            fresh = []
            for c in cards:  # featured cards repeat on every page
                if c["slug"] not in seen:
                    seen.add(c["slug"])
                    fresh.append(c)
            if not fresh:
                break
            cards = fresh

            to_fetch = []
            for card in cards:
                event_id = f"{self.name}:{card['slug']}"
                if ctx.is_unchanged(event_id, card["fingerprint"]):
                    yield Unchanged(event_id, card["categories"], card["title"])
                else:
                    to_fetch.append(card)

            events = []
            for card in to_fetch:
                event = self._detail(card)
                if event:
                    events.append(event)
                    if ctx.today <= event.end_date and event.start_date <= ctx.horizon:
                        yield event
            # The listing is roughly chronological: once a whole page starts
            # past the horizon there is nothing left worth fetching.
            if events and len(events) == len(cards) and all(e.start_date > ctx.horizon for e in events):
                break
