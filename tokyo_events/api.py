"""Read-only JSON API over the event store, plus static hosting of the web UI."""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import date
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .dedupe import merge
from .models import CATEGORIES
from .scrape import tokyo_today
from .sources import REGISTRY
from .store import EventStore

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_RANGE_DAYS = 400
log = logging.getLogger("tokyo_events.api")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _csv(qs: dict, key: str) -> list[str]:
    return [v for raw in qs.get(key, []) for v in raw.split(",") if v]


def build_meta(store: EventStore) -> dict:
    s = store.summary()
    return {
        "today": tokyo_today().date().isoformat(),
        "timezone": "Asia/Tokyo",
        "total": s["total"],
        "minDate": s["min_date"],
        "maxDate": s["max_date"],
        "categories": [{"slug": k, "label": v} for k, v in CATEGORIES.items()],
        "sources": [
            {
                "name": cls.name,
                "label": cls.label,
                "short": cls.short,
                "color": cls.color,
                "homepage": cls.homepage,
                "defaultOn": cls.default_on,
                "count": s["counts"].get(cls.name, 0),
                "lastSuccess": s["last_success"].get(cls.name),
            }
            for cls in sorted(REGISTRY.values(), key=lambda c: c.priority)
            if cls.enabled or s["counts"].get(cls.name)
        ],
    }


class Handler(SimpleHTTPRequestHandler):
    db_path: str | None = None
    _local = threading.local()

    @property
    def store(self) -> EventStore:
        # sqlite connections are per thread; the server is threaded.
        if getattr(self._local, "store", None) is None:
            self._local.store = EventStore(self.db_path)
        return self._local.store

    def log_message(self, fmt, *args):
        log.debug("%s " + fmt, self.address_string(), *args)

    def do_GET(self):
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            return super().do_GET()
        qs = parse_qs(url.query)
        try:
            if url.path == "/api/meta":
                body = self.meta()
            elif url.path == "/api/events":
                body = self.events(qs)
            else:
                raise ApiError(404, "not found")
            self._json(200, body)
        except ApiError as e:
            self._json(e.status, {"error": str(e)})

    def end_headers(self):
        if not urlparse(self.path).path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def meta(self) -> dict:
        return build_meta(self.store)

    def events(self, qs: dict) -> dict:
        today = tokyo_today().date().isoformat()
        date_from = qs.get("from", [today])[0]
        date_to = qs.get("to", [date_from])[0]
        if not (DATE_RE.match(date_from) and DATE_RE.match(date_to)):
            raise ApiError(400, "from/to must be YYYY-MM-DD")
        try:
            span = (date.fromisoformat(date_to) - date.fromisoformat(date_from)).days
        except ValueError:
            raise ApiError(400, "invalid date")
        if span < 0:
            raise ApiError(400, "'to' is before 'from'")
        if span > MAX_RANGE_DAYS:
            raise ApiError(400, f"range is limited to {MAX_RANGE_DAYS} days")

        categories = set(_csv(qs, "categories"))
        sources = set(_csv(qs, "sources"))
        text = (qs.get("q", [""])[0] or "").strip()[:100] or None

        groups: dict[str, list[dict]] = {}
        for r in self.store.query(date_from, date_to):
            groups.setdefault(r.pop("dup_group") or r["id"], []).append(r)
        if text:
            # A duplicate group matches if any source's wording matches; keep it whole.
            needle = text.lower()
            groups = {
                k: members for k, members in groups.items()
                if any(needle in (m.get(f) or "").lower()
                       for m in members for f in ("title", "summary", "venue_name", "area"))
            }

        # Facets ignore their own filter so each chip can show what it would add.
        source_facets: dict[str, int] = {}
        for members in groups.values():
            for name in {m["source"] for m in members}:
                source_facets[name] = source_facets.get(name, 0) + 1

        priority = {cls.name: cls.priority for cls in REGISTRY.values()}
        labels = {cls.name: cls.label for cls in REGISTRY.values()}
        merged = []
        for members in groups.values():
            if sources:
                members = [m for m in members if m["source"] in sources]
            if members:
                merged.append(merge(members, priority, labels))

        facets: dict[str, int] = {}
        for e in merged:
            for c in e["categories"]:
                facets[c] = facets.get(c, 0) + 1
        events = [e for e in merged if categories.intersection(e["categories"])] if categories else merged
        events.sort(key=lambda e: (e["date_approx"], e["start_date"], e["end_date"], e["title"]))
        return {
            "from": date_from,
            "to": date_to,
            "count": len(events),
            "facets": facets,
            "sourceFacets": source_facets,
            "events": events,
        }


def serve(host: str = "127.0.0.1", port: int = 8000, db_path: str | None = None) -> None:
    Handler.db_path = db_path
    EventStore(db_path).close()  # create the schema up front so an empty DB still works
    server = ThreadingHTTPServer((host, port), partial(Handler, directory=str(WEB_DIR)))
    print(f"Tokyo Events → http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
