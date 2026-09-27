"""Cross-source duplicate detection and merging.

The same exhibition or festival is often listed by several sources. After a
scrape we cluster records that describe the same happening (similar title,
overlapping dates, nearby location, different sources) and store a shared
`dup_group` on each. The API then shows one merged event per group.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from datetime import date

# Words that say little about *which* event it is.
STOPWORDS = {
    "the", "a", "an", "of", "in", "at", "on", "and", "for", "to", "with", "by", "from", "de", "no",
    "tokyo", "japan", "exhibition", "exhibit", "festival", "matsuri", "event", "special", "fair",
    "show", "live", "presents", "edition", "annual", "vol", "part", "st", "nd", "rd", "th",
}


def normalize_title(title: str) -> str:
    t = unicodedata.normalize("NFKD", title)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower().replace("&", " and ")
    t = re.sub(r"\b(19|20)\d{2}\b", " ", t)  # years
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def title_tokens(norm: str) -> set[str]:
    return {w for w in norm.split() if w not in STOPWORDS and len(w) > 1}


def _numbers(tokens: set[str]) -> set[str]:
    return {re.sub(r"\D", "", t) for t in tokens if re.search(r"\d", t)}


def title_similarity(a: dict, b: dict) -> float:
    """Word-overlap similarity, ignoring words that just name either venue.

    Titles often embed the venue ("... at Tokyo Tower"), which makes unrelated
    events at the same place look alike.
    """
    venue_words = a["_venue_tokens"] | b["_venue_tokens"]
    ta = (a["_tokens"] - venue_words) or a["_tokens"]
    tb = (b["_tokens"] - venue_words) or b["_tokens"]
    if not ta or not tb:
        return 0.0
    na, nb = _numbers(ta), _numbers(tb)
    if na and nb and not na & nb:
        return 0.0  # "10th Anniversary" vs "15th Anniversary"
    inter = len(ta & tb)
    jaccard = inter / len(ta | tb)
    small = min(len(ta), len(tb))
    containment = inter / small if small >= 2 else 0.0  # "Foo Bar" vs "Foo Bar: Subtitle at Venue"
    return max(jaccard, 0.9 * containment)


def distance_km(a: dict, b: dict) -> float | None:
    if a["lat"] is None or b["lat"] is None:
        return None
    lat1, lng1, lat2, lng2 = map(math.radians, (a["lat"], a["lng"], b["lat"], b["lng"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _days_apart(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def is_duplicate(a: dict, b: dict) -> bool:
    if a["source"] == b["source"]:
        return False  # a source doesn't list the same event twice under different ids
    if a["start_date"] > b["end_date"] or b["start_date"] > a["end_date"]:
        return False
    sim = title_similarity(a, b)
    if sim < 0.4:
        return False
    d = distance_km(a, b)
    exact = a["geo_precision"] == "venue" and b["geo_precision"] == "venue"
    if exact and d is not None:
        return (d <= 0.3 and sim >= 0.5) or (d <= 2 and sim >= 0.6) or (d <= 10 and sim >= 0.85)
    # At least one side only has an area-level (or no) location: lean on the title and dates.
    if d is not None and d > 8:
        return False
    if d is not None and d <= 1 and sim >= 0.6:
        return True
    return sim >= 0.75 and _days_apart(a["start_date"], b["start_date"]) <= 7


def find_groups(rows: list[dict]) -> dict[str, str]:
    """Map each event id to its group key (the id of one member, stable per group)."""
    for r in rows:
        r["_tokens"] = title_tokens(normalize_title(r["title"]))
        r["_venue_tokens"] = title_tokens(normalize_title(r.get("venue_name") or ""))

    # Only compare records sharing at least one meaningful title word.
    index: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        for tok in r["_tokens"]:
            index[tok].append(i)

    parent = list(range(len(rows)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    checked: set[tuple[int, int]] = set()
    for members in index.values():
        if len(members) > 300:  # a very common word: not a useful signal on its own
            continue
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                i, j = members[x], members[y]
                if (i, j) in checked:
                    continue
                checked.add((i, j))
                if root(i) != root(j) and is_duplicate(rows[i], rows[j]):
                    parent[root(i)] = root(j)

    clusters: dict[int, list[str]] = defaultdict(list)
    for i, r in enumerate(rows):
        clusters[root(i)].append(r["id"])
    return {eid: min(ids) for ids in clusters.values() for eid in ids}


FILL_FIELDS = ["image", "summary", "time_text", "price_text", "price_min", "price_max",
               "venue_name", "venue_address", "area", "station"]


def _completeness(ev: dict) -> int:
    return sum(1 for f in FILL_FIELDS if ev.get(f)) + (3 if ev.get("geo_precision") == "venue" else 0)


def merge(members: list[dict], priority: dict[str, int], labels: dict[str, str] | None = None) -> dict:
    """Combine records of one happening into a single event for display."""
    ordered = sorted(members, key=lambda e: (priority.get(e["source"], 99), -_completeness(e), e["id"]))
    out = dict(ordered[0])
    others = ordered[1:]
    for field in FILL_FIELDS:
        if not out.get(field):
            out[field] = next((o[field] for o in others if o.get(field)), out.get(field))
    if out.get("geo_precision") != "venue":
        better = next((o for o in others if o.get("geo_precision") == "venue"), None)
        if better:
            out.update(lat=better["lat"], lng=better["lng"], geo_precision="venue",
                       venue_name=out.get("venue_name") or better.get("venue_name"))
    if out.get("date_approx"):
        exact = next((o for o in others if not o.get("date_approx")), None)
        if exact:
            out.update(start_date=exact["start_date"], end_date=exact["end_date"],
                       date_approx=False, date_label=None)
    if out.get("summary") and others:
        # Prefer the fuller description when the base one is a one-liner.
        longest = max((o.get("summary") or "" for o in ordered), key=len)
        if len(out["summary"]) < 80 <= len(longest):
            out["summary"] = longest
    out["categories"] = sorted({c for m in ordered for c in m["categories"]} - {"other"}) or ["other"]
    out["ids"] = [m["id"] for m in ordered]
    out["sources"] = [
        {"name": m["source"], "label": (labels or {}).get(m["source"], m["source"]), "url": m["url"]}
        for m in ordered
    ]
    out.pop("dup_group", None)
    return out
