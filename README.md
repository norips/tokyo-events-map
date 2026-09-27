# Tokyo Events Map

Every event happening in Tokyo, on a map. Browse by **day, week, month or a custom range**, filter by **event type**, search, and open any event for dates, venue, price, directions and a calendar file.

Events currently come from [Tokyo Cheapo](https://tokyocheapo.com/events/); the code is built so more sources can be added.

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
- **`tokyo_events/scrape.py`**: runs sources, fills missing coordinates (centroid of known venues in the same area, then cached OSM Nominatim lookup of the area), upserts in batches, records each run, prunes events that ended more than 30 days ago.
- **`tokyo_events/store.py`**: SQLite schema and queries (WAL mode, so scraping can run while the site is being served).
- **`tokyo_events/api.py`**: read-only JSON API plus static hosting of `web/`.
  - `GET /api/meta`: categories, sources, counts, last successful scrape.
  - `GET /api/events?from=YYYY-MM-DD&to=YYYY-MM-DD[&categories=a,b][&sources=x][&q=text]`: events overlapping the range, plus per-category counts (`facets`) for the range.
- **`web/`**: `index.html`, `styles.css`, `app.js`. Map by MapLibre GL + OpenFreeMap vector tiles (no API key).

### Adding a source

1. Create `tokyo_events/sources/<name>.py` with a subclass of `EventSource` decorated with `@register`, setting `name`, `label`, `homepage` and implementing `scrape(ctx)` that yields `Event`s (or `Unchanged(id)` for items it can skip).
2. Map the site's categories with `normalize_category()` (add aliases in `models.py` if needed).
3. Import the module at the bottom of `tokyo_events/sources/__init__.py`.

Nothing in the store, API or UI needs to change; the UI shows every source in its header and on each event.

### About Tokyo Cheapo scraping

- Listing pages give the title, type, area, price and hours; each event page adds exact dates (schema.org JSON-LD) and coordinates.
- Events with only fuzzy dates ("Mid Apr 2027") are stored as approximate ranges (early = 1–10, mid = 11–20, late = 21–end) and labelled *TBC* in the UI.
- The site sits behind a bot firewall, so the crawler is sequential and waits 1.5 s between requests. If it gets challenged, it stops, keeps what it has (run status `partial`) and resumes on the next run. Unchanged events (same listing-card fingerprint, refreshed within 7 days) are skipped.

To keep the data fresh, schedule the scraper, for example every 6 hours with cron:

```cron
0 */6 * * * cd /path/to/Tokyo_events && pixi run scrape >> data/scrape.log 2>&1
```

## Using the site

- **Day / Week / Month / Custom**: pick the range; ← / → (or the arrows) step through periods; click the label to jump to a date.
- The **bar rail** shows events per day in the range; click a bar to open that day.
- **Type chips** filter by category (multi-select), with counts for the current range.
- **In map view** limits the list to what's visible on the map.
- Filled pins are exact venues; hollow rings mean the source only gave a neighbourhood.
- Shortcuts: `/` search, `d` `w` `m` `c` switch mode, `Esc` close. The URL keeps the full state, so views can be shared.

## Tests

```bash
pixi run test
```
