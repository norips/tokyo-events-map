"""Scrape pipeline: run sources and write normalized events into the store.

This never talks to the web UI; the UI only reads the store via the API.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .dedupe import find_groups
from .geocode import AreaGeocoder
from .http import Blocked
from .models import Event, is_known_category, normalize_categories
from .sources import get_source, enabled_sources
from .sources.base import EventSource, ScrapeContext, Unchanged
from .store import EventStore

log = logging.getLogger("tokyo_events.scrape")
JST = timezone(timedelta(hours=9))
REFRESH_AFTER = timedelta(days=7)  # re-fetch details even if the listing card is unchanged


def tokyo_today() -> datetime:
    return datetime.now(JST)


def _area_centroids(store: EventStore, batch: list[Event]) -> dict[str, tuple[float, float]]:
    points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in store.conn.execute(
        "SELECT area, lat, lng FROM events WHERE geo_precision = 'venue' AND area IS NOT NULL"
    ):
        points[r["area"]].append((r["lat"], r["lng"]))
    for e in batch:
        if e.geo_precision == "venue" and e.area:
            points[e.area].append((e.lat, e.lng))
    return {
        area: (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        for area, pts in points.items()
    }


def run_source(source: EventSource, store: EventStore, days: int, max_pages: int) -> dict:
    now = tokyo_today()
    known = store.fingerprints(source.name)

    def is_unchanged(event_id: str, fingerprint: str) -> bool:
        stored = known.get(event_id)
        if not stored or stored[0] != fingerprint:
            return False
        scraped_at = datetime.fromisoformat(stored[1])
        return datetime.now(timezone.utc) - scraped_at < REFRESH_AFTER

    ctx = ScrapeContext(
        horizon=(now + timedelta(days=days)).date().isoformat(),
        today=now.date().isoformat(),
        is_unchanged=is_unchanged,
        max_pages=max_pages,
    )
    run_id = store.start_run(source.name)
    geocoder = AreaGeocoder(store)
    stats = {"source": source.name, "seen": 0, "inserted": 0, "updated": 0,
             "unchanged": 0, "without_location": 0, "status": "ok"}
    pending: list[Event] = []
    unchanged: list[str] = []
    unknown_categories: set[str] = set()

    def flush() -> None:
        # Events with no venue coordinates fall back to their neighbourhood:
        # the centroid of known venues there, else a geocoded area centre.
        centroids = _area_centroids(store, pending)
        for e in pending:
            if e.lat is not None:
                continue
            if e.area:
                point = centroids.get(e.area) or geocoder.lookup(e.area)
            elif e.venue_name:  # e.g. a named museum; shown as approximate
                point = geocoder.lookup(e.venue_name)
            else:
                point = None
            if point:
                e.lat, e.lng = point
                e.geo_precision = "area"
        inserted, updated = store.upsert_many(pending)
        store.touch(unchanged)
        stats["inserted"] += inserted
        stats["updated"] += updated
        stats["unchanged"] += len(unchanged)
        stats["without_location"] += sum(1 for e in pending if e.lat is None)
        pending.clear()
        unchanged.clear()

    try:
        # Flush in small batches so an interrupted run keeps its progress.
        for item in source.scrape(ctx):
            stats["seen"] += 1
            raw_categories = item.source_categories or []
            unknown_categories.update(c for c in raw_categories if not is_known_category(c))
            if isinstance(item, Unchanged):
                unchanged.append(item.id)
                if item.source_categories is not None:
                    store.set_categories(item.id, raw_categories, normalize_categories(raw_categories, item.title))
            else:
                pending.append(item)
            if len(pending) + len(unchanged) >= 20:
                flush()
                log.info("%s: %d items saved", source.name, stats["seen"])
    except Blocked as exc:
        log.warning("%s blocked the crawler (%s); keeping partial results, retry later", source.name, exc)
        stats["status"] = "partial"
    except Exception as exc:
        flush()
        store.finish_run(run_id, "error", stats["seen"], stats["inserted"], stats["updated"], repr(exc))
        raise
    flush()
    if unknown_categories:
        log.warning("%s: unmapped categories (filed as 'other'): %s; add them to CATEGORY_ALIASES",
                    source.name, ", ".join(sorted(unknown_categories)))
    store.finish_run(run_id, stats["status"], stats["seen"], stats["inserted"], stats["updated"])
    return stats


def dedupe_store(store: EventStore) -> dict:
    groups = find_groups(store.dedupe_rows())
    store.set_groups(groups)
    sizes: dict[str, int] = defaultdict(int)
    for g in groups.values():
        sizes[g] += 1
    merged = sum(n for n in sizes.values() if n > 1)
    stats = {"records": len(groups), "events": len(sizes), "records_in_duplicate_groups": merged}
    log.info("Dedupe: %(records)d records -> %(events)d events (%(records_in_duplicate_groups)d records merged)", stats)
    return stats


def run(source_names: list[str] | None, days: int = 365, max_pages: int = 60, db_path=None) -> list[dict]:
    classes = [get_source(n) for n in source_names] if source_names else enabled_sources()
    store = EventStore(db_path)
    try:
        results = []
        for cls in classes:
            log.info("Scraping %s (horizon %d days)", cls.name, days)
            try:
                results.append(run_source(cls(), store, days, max_pages))
            except Exception:  # one broken source must not take the others down
                log.exception("%s failed", cls.name)
                results.append({"source": cls.name, "status": "error"})
        pruned = store.prune_ended_before((tokyo_today() - timedelta(days=30)).date().isoformat())
        if pruned:
            log.info("Pruned %d events that ended over 30 days ago", pruned)
        dedupe_store(store)
        return results
    finally:
        store.close()
