# Tokyo Events Map

Every event happening in Tokyo, on a map. Browse by **day, week, weekend or a custom range**, filter by **event type** and **source**, search, and open any event for dates, venue, price, directions and a calendar file.

Events come from three sources, merged and de-duplicated:

| Source | What it adds | How it's read |
| --- | --- | --- |
| [Tokyo Cheapo](https://tokyocheapo.com/events/) | Festivals, markets, seasonal events; area + nearest station | Listing pages + each event page (JSON-LD, map data) |
| [Tokyo Weekender](https://www.tokyoweekender.com/events/) | Curated events with hours and prices | Public WordPress REST API |
| [Tokyo Art Beat](https://www.tokyoartbeat.com/en/events) | Hundreds of gallery and museum exhibitions | One listing page with embedded JSON (Greater Tokyo only, permanent collections skipped) |

Pure Python 3 standard library (3.11+) on the back end, no build step on the front end. The environment is managed with [pixi](https://pixi.sh).

## Quick start

```bash
pixi run scrape     # fill data/events.db (first run ~30 min, later runs are incremental)
pixi run serve      # http://127.0.0.1:8000
```

| Task | What it does |
| --- | --- |
| `pixi run scrape` | Fetch events from all enabled sources into the database |
| `pixi run serve` | Run the API + website |
| `pixi run start` | Scrape, then serve |
| `pixi run build-site` | Write a static copy of the site to `_site/` (what GitHub Pages serves) |
| `pixi run dedupe` | Recompute cross-source duplicate groups (also runs after every scrape) |
| `pixi run stats` | Summary of the database |
| `pixi run sources` | List registered sources |
| `pixi run test` | Run the test suite |
| `pixi run clean-cache` | Delete the HTTP page cache |

Extra arguments are forwarded: `pixi run scrape --days 90 --source tokyocheapo`, `pixi run serve --port 9000 --host 0.0.0.0`.
Use `--db PATH` (or `TE_DB_PATH`) to point at another SQLite file. Without pixi, the same commands work as `python3 -m tokyo_events <command>`.

## Architecture

Scraping and display are fully decoupled; the SQLite database is the only thing they share.

```
 sources/ (tokyocheapo, …) ──► scrape pipeline ──► SQLite (data/events.db) ──► read-only API ──► web UI
        fetch + normalize        geocode fallback     events, event_categories     /api/meta        MapLibre map
                                 incremental upsert   scrape_runs, geocode_cache   /api/events      (web/)
```

- **`tokyo_events/models.py`**: the source-agnostic `Event` and the canonical category taxonomy (plus aliases).
- **`tokyo_events/sources/`**: one adapter per site. Each yields normalized `Event`s.
- **`tokyo_events/scrape.py`**: runs sources (one failing source doesn't stop the others), fills missing coordinates (centroid of known venues in the same area, then a cached OSM Nominatim lookup of the area or venue name), upserts in batches, records each run, prunes events that ended more than 30 days ago, then de-duplicates.
- **`tokyo_events/dedupe.py`**: clusters records from different sources that describe the same happening and merges them for display (see below).
- **`tokyo_events/store.py`**: SQLite schema and queries (WAL mode, so scraping can run while the site is being served).
- **`tokyo_events/api.py`**: read-only JSON API plus static hosting of `web/`.
  - `GET /api/meta`: categories, sources, counts, last successful scrape.
  - `GET /api/events?from=YYYY-MM-DD&to=YYYY-MM-DD[&categories=a,b][&sources=x,y][&q=text]`: merged events overlapping the range (each with `sources` and `ids`), plus per-category (`facets`) and per-source (`sourceFacets`) counts.
- **`web/`**: `index.html`, `styles.css`, `app.js`. Map by MapLibre GL + OpenFreeMap vector tiles (no API key).

### De-duplication

Records from **different** sources are treated as the same event when their dates overlap and:

- both have exact venues and they are within 300 m with similar titles, within 2 km with clearly similar titles, or within 10 km with near-identical titles; or
- one only has an area-level location, and the titles match strongly (or fairly well within 1 km), with start dates at most a week apart.

Title similarity is word overlap after dropping years, punctuation, filler words ("exhibition", "festival", "tokyo"…) and **words from either venue's name**, so "Tokyo Tower Tanabata Festival" and "Tokyo Tower Highball Garden" stay separate. Titles with different numbers ("10th" vs "15th anniversary") never match.

Each cluster shares a `dup_group`. The API shows one event per group: the record from the highest-priority source is the base (Tokyo Cheapo, then Weekender, then Art Beat), missing fields are filled from the others, the exact venue wins over an area guess, categories are combined, and every source's link is kept. Filtering by source happens before merging, so hiding a source never hides an event another source also lists.

### Adding a source

1. Create `tokyo_events/sources/<name>.py` with a subclass of `EventSource` decorated with `@register`, setting `name`, `label`, `short` (2-letter badge), `color`, `homepage`, `priority` (lower wins when merging duplicates) and implementing `scrape(ctx)` that yields `Event`s (or `Unchanged(id)` for items it can skip).
2. Map the site's categories with `normalize_category()` (add aliases in `models.py` if needed).
3. Import the module at the bottom of `tokyo_events/sources/__init__.py`.

Nothing in the store, API or UI needs to change: the new source appears in the Sources picker, on event badges, and is included in de-duplication. The scraper logs any category it can't map, so you know which aliases to add.

### About Tokyo Cheapo scraping

- Listing pages give the title, type, area, price and hours; each event page adds exact dates (schema.org JSON-LD) and coordinates.
- Events with only fuzzy dates ("Mid Apr 2027") are stored as approximate ranges (early = 1–10, mid = 11–20, late = 21–end) and labelled *TBC* in the UI.
- The site sits behind a bot firewall, so the crawler is sequential and waits 1.5 s between requests. If it gets challenged, it stops, keeps what it has (run status `partial`) and resumes on the next run. Unchanged events (same listing-card fingerprint, refreshed within 7 days) are skipped.

To keep the data fresh, schedule the scraper, for example every 6 hours with cron:

```cron
0 */6 * * * cd /path/to/Tokyo_events && pixi run scrape >> data/scrape.log 2>&1
```

## Hosting on GitHub Pages

The site can run without a server. `pixi run build-site` writes `_site/`: the web UI plus `data/events.json`, which holds every event already de-duplicated. Each duplicate group carries a pre-merged version for every combination of its sources, so hiding a source in the browser gives exactly what the API would. The page detects the static build (`<html data-static>`) and filters in the browser. All paths are relative, so it works under `https://<user>.github.io/<repo>/`.

`.github/workflows/scrape-and-deploy.yml` does the rest, every 6 hours, on demand (**Actions → Scrape and deploy → Run workflow**), and on pushes that touch the code:

1. restores `data/events.db` from the Actions cache (so scraping stays incremental),
2. runs `pixi run scrape`, then saves the database back to the cache,
3. writes a per-source results table to the run summary, with a warning annotation if a source was blocked (`partial`) or failed,
4. builds `_site/` and deploys it to GitHub Pages.

One-time setup: push to a **public** GitHub repo, then in **Settings → Pages** set **Source** to **GitHub Actions**.

Things to know: the first run has no cached database, so it does a full Tokyo Cheapo crawl (about 20–30 minutes). Bot firewalls may treat GitHub's cloud IPs differently from a home connection; if a source keeps coming back `partial`, run the scraper from your own machine instead. GitHub may pause scheduled workflows in repos with no activity for 60 days; re-enable them from the Actions tab.

## Using the site

- **Day / Week / Weekend / Custom**: pick the range (a whole month is a Custom range); ← / → (or the arrows) step through periods; click the label to jump to a date.
- **Weekend** covers Saturday–Sunday (on a weekday, the coming one); ← / → step weekend by weekend.
- **Sources**: toggle each source on or off; counts show how many events each lists in the range. Badges on each event show who lists it.
- The **bar rail** shows events per day in the range; click a bar to open that day.
- **Type chips** filter by category (multi-select), with counts for the current range; the busiest types show first, the rest behind "+N more".
- **In map view** limits the list to what's visible on the map.
- Filled pins are exact venues; hollow rings mean the source only gave a neighbourhood.
- Shortcuts: `/` search, `d` `w` `s` `c` switch to day / week / weekend / custom, `Esc` close. The URL keeps the full state, so views can be shared.

## Tests

```bash
pixi run test
```
