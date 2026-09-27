"""Static site export for hosting without a server (e.g. GitHub Pages).

Writes the web UI plus one data file holding every event, already
de-duplicated. The page then filters in the browser instead of calling the
API. Because hiding a source changes how a duplicate group merges, each group
carries a pre-merged variant for every combination of its sources, so the
merge logic lives only in Python.
"""

from __future__ import annotations

import json
import shutil
from itertools import combinations
from pathlib import Path

from .api import WEB_DIR, build_meta
from .dedupe import merge
from .sources import REGISTRY
from .store import EventStore


def export_events(store: EventStore) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for row in store.query("0000-01-01", "9999-12-31"):
        groups.setdefault(row.pop("dup_group") or row["id"], []).append(row)
    priority = {cls.name: cls.priority for cls in REGISTRY.values()}
    labels = {cls.name: cls.label for cls in REGISTRY.values()}
    entries = []
    for members in groups.values():
        names = sorted({m["source"] for m in members})
        variants = {
            ",".join(subset): merge([m for m in members if m["source"] in subset], priority, labels)
            for k in range(1, len(names) + 1)
            for subset in combinations(names, k)
        }
        entries.append({"sources": names, "variants": variants})
    return entries


def export_site(out_dir: str | Path, db_path: str | None = None) -> dict:
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEB_DIR, out, ignore=shutil.ignore_patterns("__*"))
    index = out / "index.html"
    index.write_text(index.read_text(encoding="utf-8").replace('<html lang="en">', '<html lang="en" data-static>', 1),
                     encoding="utf-8")

    store = EventStore(db_path)
    try:
        payload = {"meta": build_meta(store), "events": export_events(store)}
    finally:
        store.close()
    data_dir = out / "data"
    data_dir.mkdir()
    data_file = data_dir / "events.json"
    data_file.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"out": str(out), "events": len(payload["events"]), "bytes": data_file.stat().st_size}
