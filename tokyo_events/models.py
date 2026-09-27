"""Source-agnostic event model and the canonical category taxonomy.

Every source adapter converts whatever it scrapes into `Event` objects whose
`categories` use the canonical slugs below, so the store, API and UI never
need to know where an event came from.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field

# Canonical event types, in display order. Sources map their own labels onto these.
CATEGORIES: dict[str, str] = {
    "art": "Art",
    "music": "Music",
    "festival": "Festival",
    "fireworks": "Fireworks",
    "food": "Food",
    "drinks": "Drinks",
    "market": "Market",
    "nightlife": "Nightlife",
    "stage": "Stage & Film",
    "anime": "Anime",
    "gaming": "Gaming",
    "comedy": "Comedy",
    "nature": "Nature",
    "illumination": "Illumination",
    "sport": "Sport",
    "community": "Talks & Community",
    "trade-show": "Trade Show",
    "other": "Other",
}

# Loose aliases so new sources can map free-text labels without new code.
CATEGORY_ALIASES: dict[str, str] = {
    "music-2": "music",
    "concert": "music",
    "concerts": "music",
    "live-music": "music",
    "sport-2": "sport",
    "sports": "sport",
    "exhibition": "art",
    "exhibitions": "art",
    "museum": "art",
    "matsuri": "festival",
    "festivals": "festival",
    "flea-market": "market",
    "markets": "market",
    "beer": "drinks",
    "sake": "drinks",
    "games": "gaming",
    "manga": "anime",
    "light-up": "illumination",
    "illuminations": "illumination",
    "flowers": "nature",
    "expo": "trade-show",
    "convention": "trade-show",
    "hanabi": "fireworks",
    "party": "nightlife",
    "dance": "nightlife",
    "club": "nightlife",
    "clubbing": "nightlife",
    "show": "stage",
    "theater": "stage",
    "theatre": "stage",
    "film": "stage",
    "film-2": "stage",
    "cinema": "stage",
    "performance": "stage",
    "charity": "community",
    "fundraiser": "community",
    "volunteering": "community",
    "workshop": "community",
    "talk": "community",
    "talks": "community",
    "tour": "community",
    "tours": "community",
    "living": "community",
    "parade": "festival",
    "wildlife": "nature",
    "literature": "art",
    "literature-2": "art",
    "books": "art",
    "fashion": "market",
    "tech": "trade-show",
    # Tokyo Weekender
    "anime-manga": "anime",
    "pop-culture": "anime",
    "museums-exhibitions": "art",
    "food-drinks": "food",
    "markets-festivals": "festival",
    "nature-parks-outdoors": "nature",
    "seasons-holidays": "festival",
    "theater-dance-performance": "stage",
    "family-kids": "community",
    "therapy": "community",
    "travel": "community",
    "tw-collabs": "other",
    # Tokyo Art Beat
    "painting": "art",
    "sculpture": "art",
    "photography": "art",
    "installation": "art",
    "drawing": "art",
    "illustration": "art",
    "nihonga-ukiyoe": "art",
    "ceramics-lacquer": "art",
    "craft-folkcraft": "art",
    "prints": "art",
    "fashion-textile-design": "art",
    "archeology-history-folklore": "art",
    "graphics": "art",
    "product": "art",
    "media-arts": "art",
    "architecture": "art",
    "artist-in-residence": "art",
    "handicraft": "art",
    "picture-book": "art",
    "calligraphy": "art",
    "design": "art",
    "video-and-film": "stage",
    "performance-art": "stage",
    "talks": "community",
    "workshops": "community",
    "sound": "music",
    "manga-comics": "anime",
    "animation": "anime",
    "art-festival": "festival",
    "art-fair": "art",
    "art-competition": "art",
    "nature-science": "nature",
}


def _slug(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", raw.strip().lower()).strip("-")


def normalize_category(raw: str) -> str:
    slug = _slug(raw)
    if slug in CATEGORIES:
        return slug
    return CATEGORY_ALIASES.get(slug, "other")


# Fallback for items a source publishes without any category: (title regex, category).
TITLE_HINTS: list[tuple[str, str]] = [
    (r"firework|hanabi", "fireworks"),
    (r"festival|matsuri|parade|diwali|pilgrimage", "festival"),
    (r"market|boroichi|flea|bazaar", "market"),
    (r"christmas|illumination|light[- ]?up", "illumination"),
    (r"exhibition|museum|gallery", "art"),
    (r"concert|live music|jazz", "music"),
    (r"chrysanthemum|blossom|flower|autumn leaves|koyo", "nature"),
    (r"beer|sake|wine", "drinks"),
    (r"film|cinema|theat", "stage"),
]


def normalize_categories(raw: list[str], title: str = "") -> list[str]:
    cats = {normalize_category(c) for c in raw}
    if not raw and title:
        cats = {cat for pattern, cat in TITLE_HINTS if re.search(pattern, title, re.I)}
    return sorted(cats) or ["other"]


def is_known_category(raw: str) -> bool:
    return _slug(raw) in CATEGORIES or _slug(raw) in CATEGORY_ALIASES


@dataclass
class Event:
    source: str  # registry name of the source, e.g. "tokyocheapo"
    source_id: str  # stable id within that source
    url: str
    title: str
    start_date: str  # YYYY-MM-DD, Tokyo local date
    end_date: str  # YYYY-MM-DD inclusive
    summary: str = ""
    image: str | None = None
    start_time: str | None = None  # HH:MM
    end_time: str | None = None
    time_text: str | None = None  # human text as published, e.g. "11:00am – 8:00pm"
    date_approx: bool = False  # True when the source only gives "Mid Apr" style dates
    date_label: str | None = None  # e.g. "Mid Apr 2027"
    categories: list[str] = field(default_factory=list)  # canonical slugs
    source_categories: list[str] = field(default_factory=list)  # as published, kept for re-normalizing
    price_text: str | None = None
    price_min: float | None = None
    price_max: float | None = None
    venue_name: str | None = None
    venue_address: str | None = None
    area: str | None = None  # neighbourhood label, e.g. "Shibuya"
    station: str | None = None  # e.g. "324 m from Ōikeibajōmae Station"
    lat: float | None = None
    lng: float | None = None
    geo_precision: str | None = None  # "venue" | "area" | None
    fingerprint: str | None = None  # source-defined hash used to skip re-scraping unchanged items

    @property
    def id(self) -> str:
        return f"{self.source}:{self.source_id}"

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["id"] = self.id
        return d
