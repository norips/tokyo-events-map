"""Turn `tokyo_events scrape` JSON lines into a job summary table and warnings."""

import json
import os
import sys

rows = [json.loads(line) for line in open(sys.argv[1]) if line.startswith("{")]
lines = [
    "## Scrape results",
    "| Source | Status | Seen | New | Updated | Unchanged |",
    "| --- | --- | --- | --- | --- | --- |",
]
for s in rows:
    cells = [s["source"], s.get("status", "?")] + [str(s.get(k, "-")) for k in ("seen", "inserted", "updated", "unchanged")]
    lines.append("| " + " | ".join(cells) + " |")
    if s.get("status") != "ok":
        # Shows up as an annotation on the run page, e.g. when a bot challenge stopped a source.
        print(f"::warning title={s['source']} {s.get('status')}::{json.dumps(s)}")
if not rows:
    lines.append("| (no results) | | | | | |")

with open(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/stdout"), "a") as f:
    f.write("\n".join(lines) + "\n")
