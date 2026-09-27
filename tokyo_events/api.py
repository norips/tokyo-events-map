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
        s = self.store.summary()
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
                    "homepage": cls.homepage,
                    "count": s["counts"].get(cls.name, 0),
                    "lastSuccess": s["last_success"].get(cls.name),
                }
                for cls in REGISTRY.values()
            ],
        }

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

        categories = _csv(qs, "categories")
        text = (qs.get("q", [""])[0] or "").strip()[:100] or None
        # Facet counts ignore the category filter so the chips can show what
        # each type would add.
        base = self.store.query(date_from, date_to, sources=_csv(qs, "sources") or None, text=text)
        facets: dict[str, int] = {}
        for e in base:
            for c in e["categories"]:
                facets[c] = facets.get(c, 0) + 1
        selected = set(categories)
        events = [e for e in base if selected.intersection(e["categories"])] if selected else base
        return {"from": date_from, "to": date_to, "count": len(events), "facets": facets, "events": events}


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
