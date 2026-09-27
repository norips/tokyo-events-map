import json
import tempfile
import threading
import unittest
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

from tokyo_events import api
from tokyo_events.models import Event, normalize_category
from tokyo_events.sources.base import ScrapeContext, Unchanged
from tokyo_events.sources.tokyocheapo import (
    TokyoCheapo, _resolve_date, build_event, parse_detail, parse_listing,
)
from tokyo_events.store import EventStore

FIXTURES = Path(__file__).parent / "fixtures"


def ev(slug, start, end, cats=("art",), **kw):
    return Event(source="test", source_id=slug, url=f"https://x/{slug}", title=slug.title(),
                 start_date=start, end_date=end, categories=list(cats), **kw)


class ParsingTests(unittest.TestCase):
    def setUp(self):
        self.cards = parse_listing((FIXTURES / "listing.html").read_text())
        self.detail = parse_detail((FIXTURES / "detail.html").read_text())

    def test_listing_cards(self):
        self.assertEqual(len(self.cards), 5)
        first = self.cards[0]
        self.assertEqual(first["slug"], "mugen-no-nikukyu-monsoni-pop-up-omotesando")
        self.assertEqual(first["categories"], ["anime", "food", "gaming"])
        self.assertEqual(first["area"], "Omotesandō")
        self.assertEqual(first["price_text"], "Free")
        self.assertEqual(first["time_text"], "11:00am – 8:00pm")
        self.assertTrue(first["excerpt"].startswith("MIXI is building"))

    def test_unconfirmed_card_qualifiers(self):
        regatta = self.cards[-1]
        self.assertEqual(regatta["date_text"], "Mid Apr 2027")
        self.assertEqual(regatta["qualifiers"], ["Mid"])

    def test_detail(self):
        self.assertEqual(self.detail["ld"]["startDate"], "2026-08-22T09:00+09:00")
        self.assertAlmostEqual(float(self.detail["map"]["lat"]), 35.593707)
        self.assertIn("Station", self.detail["station"])

    def test_build_event(self):
        e = build_event(self.cards[0], self.detail)
        self.assertEqual((e.start_date, e.end_date), ("2026-08-22", "2026-08-23"))
        self.assertEqual((e.start_time, e.end_time), ("09:00", "14:30"))
        self.assertEqual(e.venue_name, "Oi Racecourse")
        self.assertEqual(e.geo_precision, "venue")
        self.assertEqual(e.categories, ["anime", "food", "gaming"])
        self.assertEqual(e.id, "tokyocheapo:mugen-no-nikukyu-monsoni-pop-up-omotesando")

    def test_approximate_dates(self):
        self.assertEqual(_resolve_date("2027-04", "Mid", False), ("2027-04-11", True))
        self.assertEqual(_resolve_date("2027-04", "Mid", True), ("2027-04-20", True))
        self.assertEqual(_resolve_date("2027-02", "Late", True), ("2027-02-28", True))
        self.assertEqual(_resolve_date("2027-02", None, False), ("2027-02-01", True))
        self.assertEqual(_resolve_date("2026-10-03T10:00+09:00", None, False), ("2026-10-03", False))
        self.assertIsNone(_resolve_date("", None, False))

    def test_category_normalization(self):
        self.assertEqual(normalize_category("music-2"), "music")
        self.assertEqual(normalize_category("Trade Show"), "trade-show")
        self.assertEqual(normalize_category("something-new"), "other")
        self.assertEqual(normalize_category("fireworks"), "fireworks")
        self.assertEqual(normalize_category("party"), "nightlife")
        self.assertEqual(normalize_category("film-2"), "stage")
        self.assertEqual(normalize_category("fundraiser"), "community")

    def test_title_fallback_only_without_source_categories(self):
        from tokyo_events.models import normalize_categories
        self.assertEqual(normalize_categories([], "Setagaya Boroichi Market"), ["market"])
        self.assertEqual(normalize_categories([], "Akibasan Fire Festival"), ["festival"])
        self.assertEqual(normalize_categories([], "Usokae Bullfinch Exchange"), ["other"])
        self.assertEqual(normalize_categories(["art"], "Night Market"), ["art"])


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url):
        from tokyo_events.http import NotFound
        self.calls.append(url)
        if url not in self.pages:
            raise NotFound(url)
        return self.pages[url]


class SourceTests(unittest.TestCase):
    def test_scrape_dedupes_and_skips_unchanged(self):
        listing = (FIXTURES / "listing.html").read_text()
        detail = (FIXTURES / "detail.html").read_text()
        cards = parse_listing(listing)
        pages = {"https://tokyocheapo.com/events/": listing}
        pages.update({c["url"]: detail for c in cards})
        fetcher = FakeFetcher(pages)
        unchanged_id = f"tokyocheapo:{cards[1]['slug']}"
        ctx = ScrapeContext(horizon="2027-12-31", today="2026-08-01",
                            is_unchanged=lambda i, fp: i == unchanged_id)
        items = list(TokyoCheapo(fetcher).scrape(ctx))
        self.assertIn(Unchanged(unchanged_id, ["sport-2"], "September Grand Sumo Tournament"), items)
        # 5 cards, one repeated in the fixture, one unchanged -> 3 detail fetches
        detail_calls = [u for u in fetcher.calls if u != "https://tokyocheapo.com/events/"
                        and "/page/" not in u]
        self.assertEqual(len(detail_calls), 3)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.tmp.name) / "t.db")
        self.store.upsert_many([
            ev("long-show", "2026-09-01", "2026-10-31", ("art",)),
            ev("one-day", "2026-09-27", "2026-09-27", ("music", "festival")),
            ev("next-month", "2026-10-10", "2026-10-12", ("food",), summary="Ramen feast"),
        ])

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def ids(self, rows):
        return sorted(r["source_id"] for r in rows)

    def test_overlap_query(self):
        self.assertEqual(self.ids(self.store.query("2026-09-27", "2026-09-27")), ["long-show", "one-day"])
        self.assertEqual(self.ids(self.store.query("2026-10-01", "2026-10-31")), ["long-show", "next-month"])
        self.assertEqual(self.ids(self.store.query("2026-11-01", "2026-11-30")), [])

    def test_category_and_text_filters(self):
        self.assertEqual(self.ids(self.store.query("2026-09-01", "2026-12-31", categories=["festival"])), ["one-day"])
        self.assertEqual(self.ids(self.store.query("2026-09-01", "2026-12-31", text="ramen")), ["next-month"])

    def test_upsert_updates_and_replaces_categories(self):
        ins, upd = self.store.upsert_many([ev("one-day", "2026-09-27", "2026-09-28", ("comedy",))])
        self.assertEqual((ins, upd), (0, 1))
        row = self.store.query("2026-09-28", "2026-09-28", categories=["comedy"])
        self.assertEqual(self.ids(row), ["long-show", "one-day"][1:])
        self.assertEqual(row[0]["categories"], ["comedy"])

    def test_set_categories_for_unchanged_event(self):
        self.store.set_categories("test:one-day", ["fireworks"], ["fireworks"])
        rows = self.store.query("2026-09-27", "2026-09-27", categories=["fireworks"])
        self.assertEqual(self.ids(rows), ["one-day"])

    def test_prune(self):
        self.assertEqual(self.store.prune_ended_before("2026-10-01"), 1)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        db = Path(cls.tmp.name) / "api.db"
        store = EventStore(db)
        store.upsert_many([
            ev("a", "2026-09-27", "2026-09-27", ("music",)),
            ev("b", "2026-09-20", "2026-10-05", ("art",)),
            Event(source="other", source_id="b2", url="https://y/b2", title="B",
                  start_date="2026-09-20", end_date="2026-10-05", categories=["festival"]),
        ])
        store.set_groups({"test:a": "test:a", "test:b": "test:b", "other:b2": "test:b"})
        store.close()
        api.Handler.db_path = str(db)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(api.Handler, directory=str(api.WEB_DIR)))
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_events_with_facets(self):
        status, body = self.get("/api/events?from=2026-09-27&to=2026-09-27&categories=music")
        self.assertEqual(status, 200)
        self.assertEqual([e["source_id"] for e in body["events"]], ["a"])
        self.assertEqual(body["facets"], {"music": 1, "art": 1, "festival": 1})  # facets ignore the category filter

    def test_duplicates_are_merged_and_sources_filter(self):
        status, body = self.get("/api/events?from=2026-09-27&to=2026-09-27")
        self.assertEqual(body["count"], 2)
        merged = next(e for e in body["events"] if set(e["ids"]) == {"test:b", "other:b2"})
        self.assertEqual(merged["categories"], ["art", "festival"])
        self.assertEqual(body["sourceFacets"], {"test": 2, "other": 1})
        status, body = self.get("/api/events?from=2026-09-27&to=2026-09-27&sources=other")
        self.assertEqual([e["id"] for e in body["events"]], ["other:b2"])

    def test_validation(self):
        self.assertEqual(self.get("/api/events?from=2026-10-01&to=2026-09-01")[0], 400)
        self.assertEqual(self.get("/api/events?from=nope")[0], 400)
        self.assertEqual(self.get("/api/events?from=2026-01-01&to=2028-01-01")[0], 400)

    def test_meta(self):
        status, body = self.get("/api/meta")
        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 3)
        self.assertIn("tokyocheapo", [s["name"] for s in body["sources"]])

    def test_static_index(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn(b"Tokyo", r.read())


if __name__ == "__main__":
    unittest.main()


class NewSourceTests(unittest.TestCase):
    def test_tokyoartbeat_keeps_tokyo_and_skips_permanent(self):
        from tokyo_events.sources.tokyoartbeat import build_event, listing_events
        raws = listing_events((FIXTURES / "tokyoartbeat.html").read_text())
        events = [e for e in (build_event(r) for r in raws) if e]
        self.assertEqual(len(raws), 3)
        self.assertEqual(len(events), 1)  # Kyoto is out of the box, permanent collection skipped
        e = events[0]
        self.assertEqual(e.venue_name, "Signal")
        self.assertEqual((e.start_date, e.end_date), ("2026-09-29", "2026-10-31"))
        self.assertIn("art", e.categories)
        self.assertTrue(e.image.startswith("https://") and e.image.endswith("?w=640&fm=jpg"))
        self.assertEqual(e.geo_precision, "venue")

    def test_tokyoweekender_fields(self):
        from tokyo_events.sources.tokyoweekender import build_event
        items = json.loads((FIXTURES / "tokyoweekender.json").read_text())
        slugs = {303486: "museums-exhibitions", 303485: "pop-culture"}
        e = build_event(items[0], slugs)
        self.assertEqual((e.start_date, e.end_date), ("2026-09-15", "2026-11-08"))
        self.assertEqual((e.start_time, e.end_time), ("09:30", "17:30"))
        self.assertTrue(e.time_text.startswith("9:30am – 5:30pm · Saturdays until"))
        self.assertEqual(e.price_min, 1900.0)
        self.assertAlmostEqual(e.lat, 35.6965972)
        self.assertEqual(e.categories, ["anime", "art"])
        self.assertIsNone(build_event(items[2], slugs))  # online events are skipped


def rec(id_, source, title, start, end, venue="", lat=35.66, lng=139.74, precision="venue"):
    return {"id": id_, "source": source, "title": title, "venue_name": venue, "start_date": start,
            "end_date": end, "lat": lat, "lng": lng, "geo_precision": precision}


class DedupeTests(unittest.TestCase):
    def groups(self, *rows):
        from tokyo_events.dedupe import find_groups
        g = find_groups(list(rows))
        return g[rows[0]["id"]] == g[rows[1]["id"]]

    def test_same_exhibition_reworded(self):
        self.assertTrue(self.groups(
            rec("a:1", "a", 'Mona Sugata "In Between"', "2026-10-03", "2026-10-30", "UltraSuperNew Kura"),
            rec("b:1", "b", "Mona Sugata: In Between Exhibition", "2026-10-03", "2026-11-07", "UltraSuperNew KURA",
                lng=139.741)))

    def test_area_precision_needs_strong_title_match(self):
        self.assertTrue(self.groups(
            rec("a:1", "a", "Hiroshige: New Perspectives on Edo Japan", "2026-09-29", "2026-12-20", "Ueno",
                precision="area"),
            rec("b:1", "b", "Special Exhibition: Utagawa Hiroshige — New Perspectives on Edo Japan",
                "2026-09-29", "2026-12-20", "Tokyo National Museum", lat=35.71, lng=139.77)))

    def test_different_events_at_same_venue(self):
        self.assertFalse(self.groups(
            rec("a:1", "a", "Tokyo Tower Tanabata Festival", "2026-06-27", "2026-09-27", "Tokyo Tower"),
            rec("b:1", "b", "Tokyo Tower Highball Garden 2026", "2026-03-20", "2026-10-18", "Tokyo Tower")))
        self.assertFalse(self.groups(
            rec("a:1", "a", "Sanrio Puroland Halloween", "2026-09-11", "2026-11-03", "Sanrio Puroland"),
            rec("b:1", "b", "Sanrio Puroland 35th Anniversary Event", "2025-12-05", "2026-12-31", "Sanrio Puroland")))

    def test_conflicting_numbers(self):
        self.assertFalse(self.groups(
            rec("a:1", "a", "Re:Zero 10th Anniversary Exhibition", "2026-09-12", "2026-10-04", precision="area"),
            rec("b:1", "b", "Mappa Expo 15th Anniversary Exhibition", "2026-09-16", "2026-12-07")))

    def test_no_date_overlap_or_same_source(self):
        self.assertFalse(self.groups(
            rec("a:1", "a", "Moon Art Night", "2026-09-18", "2026-10-04"),
            rec("b:1", "b", "Moon Art Night", "2027-09-18", "2027-10-04")))
        self.assertFalse(self.groups(
            rec("a:1", "a", "Moon Art Night", "2026-09-18", "2026-10-04"),
            rec("a:2", "a", "Moon Art Night", "2026-09-18", "2026-10-04")))

    def test_merge_prefers_priority_and_fills_gaps(self):
        from tokyo_events.dedupe import merge
        base = {"summary": "", "image": None, "time_text": None, "price_text": None, "price_min": None,
                "price_max": None, "venue_address": None, "area": None, "station": None, "date_approx": False,
                "date_label": None, "url": "u"}
        a = {**base, **rec("a:1", "a", "Show", "2026-10-01", "2026-10-05", "Hall", precision="area"),
             "categories": ["other"], "area": "Shibuya", "url": "https://a"}
        b = {**base, **rec("b:1", "b", "Show!", "2026-10-01", "2026-10-05", "Hall", lat=35.7),
             "categories": ["music"], "price_text": "¥1,000", "url": "https://b"}
        m = merge([b, a], {"a": 10, "b": 20}, {"a": "A", "b": "B"})
        self.assertEqual(m["id"], "a:1")  # higher priority source is the base
        self.assertEqual((m["lat"], m["geo_precision"]), (35.7, "venue"))  # exact location wins
        self.assertEqual(m["price_text"], "¥1,000")
        self.assertEqual(m["categories"], ["music"])
        self.assertEqual([s["label"] for s in m["sources"]], ["A", "B"])
        self.assertEqual(m["ids"], ["a:1", "b:1"])


class ExportTests(unittest.TestCase):
    def test_static_export_has_variants_per_source_combination(self):
        from tokyo_events.export import export_site
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "x.db"
            store = EventStore(db)
            store.upsert_many([
                ev("a", "2026-09-27", "2026-09-27", ("music",)),
                Event(source="other", source_id="a2", url="https://y/a2", title="A",
                      start_date="2026-09-27", end_date="2026-09-27", categories=["festival"]),
                ev("solo", "2026-10-01", "2026-10-02", ("art",)),
            ])
            store.set_groups({"test:a": "test:a", "other:a2": "test:a", "test:solo": "test:solo"})
            store.close()
            result = export_site(Path(tmp) / "site", str(db))
            site = Path(result["out"])
            self.assertIn("data-static", (site / "index.html").read_text())
            data = json.loads((site / "data" / "events.json").read_text())
            self.assertEqual(result["events"], 2)
            group = next(g for g in data["events"] if len(g["sources"]) == 2)
            self.assertEqual(sorted(group["variants"]), ["other", "other,test", "test"])
            self.assertEqual(group["variants"]["other,test"]["categories"], ["festival", "music"])
            self.assertEqual(group["variants"]["test"]["categories"], ["music"])
            self.assertIn("sources", data["meta"])
