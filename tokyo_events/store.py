"""SQLite event store: the only thing shared by the scraper and the web API.

The scraper writes (upserts) and the API reads; WAL mode lets both run at
the same time as separate processes.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Event

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "events.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    url           TEXT NOT NULL,
    title         TEXT NOT NULL,
    summary       TEXT,
    image         TEXT,
    start_date    TEXT NOT NULL,
    end_date      TEXT NOT NULL,
    start_time    TEXT,
    end_time      TEXT,
    time_text     TEXT,
    date_approx   INTEGER NOT NULL DEFAULT 0,
    date_label    TEXT,
    price_text    TEXT,
    price_min     REAL,
    price_max     REAL,
    venue_name    TEXT,
    venue_address TEXT,
    area          TEXT,
    station       TEXT,
    lat           REAL,
    lng           REAL,
    geo_precision TEXT,
    fingerprint   TEXT,
    source_categories TEXT,
    dup_group     TEXT,  -- events describing the same happening across sources share this
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    scraped_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_dates ON events (start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_events_source ON events (source);

CREATE TABLE IF NOT EXISTS event_categories (
    event_id TEXT NOT NULL REFERENCES events (id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    PRIMARY KEY (event_id, category)
);
CREATE INDEX IF NOT EXISTS idx_event_categories_category ON event_categories (category);

CREATE TABLE IF NOT EXISTS geocode_cache (
    query      TEXT PRIMARY KEY,
    lat        REAL,
    lng        REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,
    seen        INTEGER DEFAULT 0,
    inserted    INTEGER DEFAULT 0,
    updated     INTEGER DEFAULT 0,
    error       TEXT
);
"""

EVENT_COLUMNS = [
    "id", "source", "source_id", "url", "title", "summary", "image",
    "start_date", "end_date", "start_time", "end_time", "time_text",
    "date_approx", "date_label", "price_text", "price_min", "price_max",
    "venue_name", "venue_address", "area", "station", "lat", "lng",
    "geo_precision", "fingerprint", "source_categories",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EventStore:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path or os.environ.get("TE_DB_PATH") or DEFAULT_DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(events)")}
        if "source_categories" not in cols:
            self.conn.execute("ALTER TABLE events ADD COLUMN source_categories TEXT")
        if "dup_group" not in cols:
            self.conn.execute("ALTER TABLE events ADD COLUMN dup_group TEXT")

    def close(self) -> None:
        self.conn.close()

    # --- writes (scraper side) -------------------------------------------

    def fingerprints(self, source: str) -> dict[str, tuple[str | None, str]]:
        """id -> (fingerprint, scraped_at) for incremental scraping."""
        rows = self.conn.execute(
            "SELECT id, fingerprint, scraped_at FROM events WHERE source = ?", (source,)
        )
        return {r["id"]: (r["fingerprint"], r["scraped_at"]) for r in rows}

    def touch(self, ids: list[str]) -> None:
        """Mark unchanged events as still listed by their source."""
        now = _now()
        with self.conn:
            self.conn.executemany(
                "UPDATE events SET last_seen_at = ? WHERE id = ?", [(now, i) for i in ids]
            )

    def set_categories(self, event_id: str, source_categories: list[str], categories: list[str]) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE events SET source_categories = ? WHERE id = ?",
                (json.dumps(source_categories, ensure_ascii=False), event_id),
            )
            self.conn.execute("DELETE FROM event_categories WHERE event_id = ?", (event_id,))
            self.conn.executemany(
                "INSERT OR IGNORE INTO event_categories (event_id, category) VALUES (?, ?)",
                [(event_id, c) for c in categories],
            )

    def upsert_many(self, events: list[Event]) -> tuple[int, int]:
        now = _now()
        inserted = updated = 0
        with self.conn:
            for ev in events:
                d = ev.to_dict()
                d["date_approx"] = int(d["date_approx"])
                d["source_categories"] = json.dumps(d["source_categories"], ensure_ascii=False)
                exists = self.conn.execute(
                    "SELECT 1 FROM events WHERE id = ?", (ev.id,)
                ).fetchone()
                values = [d[c] for c in EVENT_COLUMNS]
                if exists:
                    sets = ", ".join(f"{c} = ?" for c in EVENT_COLUMNS[1:])
                    self.conn.execute(
                        f"UPDATE events SET {sets}, last_seen_at = ?, scraped_at = ? WHERE id = ?",
                        values[1:] + [now, now, ev.id],
                    )
                    updated += 1
                else:
                    cols = EVENT_COLUMNS + ["first_seen_at", "last_seen_at", "scraped_at"]
                    self.conn.execute(
                        f"INSERT INTO events ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        values + [now, now, now],
                    )
                    inserted += 1
                self.conn.execute("DELETE FROM event_categories WHERE event_id = ?", (ev.id,))
                self.conn.executemany(
                    "INSERT OR IGNORE INTO event_categories (event_id, category) VALUES (?, ?)",
                    [(ev.id, c) for c in (ev.categories or ["other"])],
                )
        return inserted, updated

    def dedupe_rows(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, source, title, venue_name, start_date, end_date, lat, lng, geo_precision FROM events"
        )
        return [dict(r) for r in rows]

    def set_groups(self, groups: dict[str, str]) -> None:
        with self.conn:
            self.conn.executemany(
                "UPDATE events SET dup_group = ? WHERE id = ?", [(g, i) for i, g in groups.items()]
            )

    def prune_ended_before(self, date: str) -> int:
        with self.conn:
            cur = self.conn.execute("DELETE FROM events WHERE end_date < ?", (date,))
        return cur.rowcount

    def geocode_lookup(self, query: str) -> tuple[float | None, float | None] | None:
        """Cached result (lat/lng may be None for a known miss) or None if never looked up."""
        r = self.conn.execute("SELECT lat, lng FROM geocode_cache WHERE query = ?", (query,)).fetchone()
        return (r["lat"], r["lng"]) if r else None

    def geocode_save(self, query: str, lat: float | None, lng: float | None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO geocode_cache (query, lat, lng, created_at) VALUES (?, ?, ?, ?)",
                (query, lat, lng, _now()),
            )

    def start_run(self, source: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO scrape_runs (source, started_at, status) VALUES (?, ?, 'running')",
                (source, _now()),
            )
        return cur.lastrowid

    def finish_run(self, run_id: int, status: str, seen=0, inserted=0, updated=0, error=None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE scrape_runs SET finished_at = ?, status = ?, seen = ?, inserted = ?, "
                "updated = ?, error = ? WHERE id = ?",
                (_now(), status, seen, inserted, updated, error, run_id),
            )

    # --- reads (API side) -------------------------------------------------

    def query(
        self,
        date_from: str,
        date_to: str,
        categories: list[str] | None = None,
        sources: list[str] | None = None,
        text: str | None = None,
    ) -> list[dict]:
        """Events overlapping [date_from, date_to] (inclusive YYYY-MM-DD)."""
        where = ["e.start_date <= ?", "e.end_date >= ?"]
        params: list = [date_to, date_from]
        if categories:
            where.append(
                f"e.id IN (SELECT event_id FROM event_categories WHERE category IN "
                f"({', '.join('?' * len(categories))}))"
            )
            params += categories
        if sources:
            where.append(f"e.source IN ({', '.join('?' * len(sources))})")
            params += sources
        if text:
            where.append(
                "(e.title LIKE ? OR e.summary LIKE ? OR e.venue_name LIKE ? OR e.area LIKE ?)"
            )
            params += [f"%{text}%"] * 4
        sql = f"""
            SELECT e.*, (SELECT group_concat(category) FROM event_categories c
                         WHERE c.event_id = e.id) AS category_list
            FROM events e
            WHERE {' AND '.join(where)}
            ORDER BY e.date_approx, e.start_date, e.end_date, e.title
        """
        out = []
        for r in self.conn.execute(sql, params):
            d = dict(r)
            d["categories"] = sorted((d.pop("category_list") or "other").split(","))
            d["date_approx"] = bool(d["date_approx"])
            for k in ("fingerprint", "first_seen_at", "source_categories", "last_seen_at", "scraped_at"):
                d.pop(k, None)
            out.append(d)
        return out

    def summary(self) -> dict:
        row = self.conn.execute(
            "SELECT count(*) AS total, min(start_date) AS min_date, max(end_date) AS max_date FROM events"
        ).fetchone()
        runs = self.conn.execute(
            """SELECT source, max(finished_at) AS last_success FROM scrape_runs
               WHERE status IN ('ok', 'partial') GROUP BY source"""
        ).fetchall()
        per_source = self.conn.execute(
            "SELECT source, count(*) AS n FROM events GROUP BY source"
        ).fetchall()
        return {
            "total": row["total"],
            "min_date": row["min_date"],
            "max_date": row["max_date"],
            "last_success": {r["source"]: r["last_success"] for r in runs},
            "counts": {r["source"]: r["n"] for r in per_source},
        }
