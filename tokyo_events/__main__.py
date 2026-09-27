"""CLI: python -m tokyo_events {scrape,serve,sources,stats}"""

from __future__ import annotations

import argparse
import json
import logging

from . import api, scrape
from .sources import REGISTRY
from .store import EventStore


def main() -> None:
    p = argparse.ArgumentParser(prog="tokyo_events")
    p.add_argument("--db", help="SQLite path (default data/events.db or $TE_DB_PATH)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scrape", help="fetch events from sources into the database")
    s.add_argument("--source", action="append", help="source name (repeatable); default: all enabled")
    s.add_argument("--days", type=int, default=365, help="how far ahead to keep events")
    s.add_argument("--max-pages", type=int, default=60)

    v = sub.add_parser("serve", help="run the API + web UI")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)

    sub.add_parser("dedupe", help="recompute cross-source duplicate groups")
    x = sub.add_parser("export", help="write a static copy of the site (no server needed)")
    x.add_argument("--out", default="_site", help="output directory (default: _site)")
    sub.add_parser("sources", help="list registered sources")
    sub.add_parser("stats", help="show database summary")

    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.cmd == "scrape":
        for stats in scrape.run(args.source, args.days, args.max_pages, args.db):
            print(json.dumps(stats))
    elif args.cmd == "serve":
        api.serve(args.host, args.port, args.db)
    elif args.cmd == "dedupe":
        store = EventStore(args.db)
        print(json.dumps(scrape.dedupe_store(store)))
        store.close()
    elif args.cmd == "export":
        from .export import export_site
        print(json.dumps(export_site(args.out, args.db)))
    elif args.cmd == "sources":
        for cls in sorted(REGISTRY.values(), key=lambda c: c.priority):
            print(f"{cls.name:15} {'enabled ' if cls.enabled else 'disabled'} {cls.label} — {cls.homepage}")
    elif args.cmd == "stats":
        store = EventStore(args.db)
        print(json.dumps(store.summary(), indent=2))
        store.close()


if __name__ == "__main__":
    main()
