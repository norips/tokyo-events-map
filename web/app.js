/* Tokyo Events Map — front end.
 * Talks only to the read-only API (/api/meta, /api/events); never to sources.
 */

const CATEGORY_COLORS = {
  art: "#8b5cf6",
  music: "#ec4899",
  festival: "#f43f5e",
  fireworks: "#f97316",
  food: "#f59e0b",
  drinks: "#14b8a6",
  market: "#84cc16",
  nightlife: "#c026d3",
  stage: "#0ea5e9",
  anime: "#6366f1",
  gaming: "#22d3ee",
  comedy: "#eab308",
  nature: "#22c55e",
  illumination: "#e879f9",
  sport: "#3b82f6",
  community: "#a16207",
  "trade-show": "#64748b",
  other: "#94a3b8",
};
const MAP_STYLES = {
  light: "https://tiles.openfreemap.org/styles/positron",
  dark: "https://tiles.openfreemap.org/styles/dark",
};
const TOKYO = { center: [139.735, 35.68], zoom: 11.2 };
const TAB_MODES = ["day", "week", "weekend", "custom"];
const MODES = [...TAB_MODES, "month"]; // "month" has no tab any more but old shared links still work

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

/* ---------- Date helpers (all dates are Tokyo-local "YYYY-MM-DD" strings) ---------- */
const D = {
  // en-GB order ("27 Sep") without its four-letter "Sept".
  f: (s, opts) => D.parse(s).toLocaleDateString("en-GB", { timeZone: "UTC", ...opts }).replace(/\bSept\b/, "Sep"),
  parse: (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); },
  fmt: (dt) => dt.toISOString().slice(0, 10),
  add: (s, days) => { const dt = D.parse(s); dt.setUTCDate(dt.getUTCDate() + days); return D.fmt(dt); },
  addMonths: (s, n) => { const dt = D.parse(s); return D.fmt(new Date(Date.UTC(dt.getUTCFullYear(), dt.getUTCMonth() + n, 1))); },
  diff: (a, b) => Math.round((D.parse(b) - D.parse(a)) / 864e5),
  dow: (s) => D.parse(s).getUTCDay(),
  tokyoToday: () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Tokyo" }).format(new Date()),
  valid: (s) => /^\d{4}-\d{2}-\d{2}$/.test(s || "") && !isNaN(D.parse(s)),
};

/* ---------- State ---------- */
const state = {
  mode: "week",
  anchor: D.tokyoToday(),
  from: null,
  to: null,
  cats: new Set(),
  sources: new Set(), // empty = all sources (no filter sent to the API)
  sourcesFromUrl: false,
  q: "",
  inView: false,
  selected: null,
  today: D.tokyoToday(),
  meta: null,
  data: null, // last /api/events response
};

function range() {
  const a = state.anchor;
  switch (state.mode) {
    case "day": return [a, a];
    case "weekend": { const sat = weekendStart(a); return [sat, D.add(sat, 1)]; }
    case "week": { const start = D.add(a, -((D.dow(a) + 6) % 7)); return [start, D.add(start, 6)]; }
    case "month": { const start = a.slice(0, 8) + "01"; return [start, D.add(D.addMonths(start, 1), -1)]; }
    default: {
      let f = state.from || a, t = state.to || D.add(a, 13);
      if (t < f) [f, t] = [t, f];
      return [f, t];
    }
  }
}

// Saturday of the weekend containing `day`, or of the coming weekend on weekdays.
function weekendStart(day) {
  const dow = D.dow(day);
  return dow === 0 ? D.add(day, -1) : D.add(day, 6 - dow);
}

function rangeLabel([f, t]) {
  const thisYear = state.today.slice(0, 4);
  const yr = (s) => (s.slice(0, 4) !== thisYear ? " " + s.slice(0, 4) : "");
  if (state.mode === "day") {
    const rel = f === state.today ? "Today · " : f === D.add(state.today, 1) ? "Tomorrow · " : "";
    return rel + D.f(f, { weekday: "short", day: "numeric", month: "short" }) + yr(f);
  }
  if (state.mode === "month") return D.f(f, { month: "long", year: "numeric" });
  if (state.mode === "weekend") {
    const thisSat = weekendStart(state.today);
    const rel = f === thisSat ? "This weekend · " : f === D.add(thisSat, 7) ? "Next weekend · " : "";
    const span = f.slice(0, 7) === t.slice(0, 7)
      ? `${D.f(f, { day: "numeric" })}–${D.f(t, { day: "numeric", month: "short" })}`
      : `${D.f(f, { day: "numeric", month: "short" })} – ${D.f(t, { day: "numeric", month: "short" })}`;
    return rel + (rel ? span : `Sat ${span}`) + yr(t);
  }
  const sameMonth = f.slice(0, 7) === t.slice(0, 7);
  return sameMonth
    ? `${D.f(f, { day: "numeric" })} – ${D.f(t, { day: "numeric", month: "short" })}${yr(t)}`
    : `${D.f(f, { day: "numeric", month: "short" })}${f.slice(0, 4) !== t.slice(0, 4) ? yr(f) : ""} – ${D.f(t, { day: "numeric", month: "short" })}${yr(t)}`;
}

function shift(dir) {
  if (state.mode === "day") state.anchor = D.add(state.anchor, dir);
  else if (state.mode === "week" || state.mode === "weekend") state.anchor = D.add(state.anchor, 7 * dir);
  else if (state.mode === "month") state.anchor = D.addMonths(state.anchor, dir);
  else {
    const [f, t] = range();
    const span = D.diff(f, t) + 1;
    state.from = D.add(f, span * dir);
    state.to = D.add(t, span * dir);
  }
  update();
}

/* ---------- URL hash <-> state ---------- */
function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (MODES.includes(p.get("mode"))) state.mode = p.get("mode");
  if (D.valid(p.get("date"))) state.anchor = p.get("date");
  if (D.valid(p.get("from"))) state.from = p.get("from");
  if (D.valid(p.get("to"))) state.to = p.get("to");
  state.cats = new Set((p.get("cats") || "").split(",").filter((c) => c in CATEGORY_COLORS));
  state.sources = new Set((p.get("src") || "").split(",").filter(Boolean));
  state.sourcesFromUrl = p.has("src");
  state.q = p.get("q") || "";
  state.selected = p.get("event") || null;
}
function writeHash() {
  const p = new URLSearchParams();
  p.set("mode", state.mode);
  if (state.mode === "custom") { const [f, t] = range(); p.set("from", f); p.set("to", t); }
  else p.set("date", state.anchor);
  if (state.cats.size) p.set("cats", [...state.cats].join(","));
  // The URL only records the source choice when it differs from the default.
  if (state.meta && !sourcesAreDefault()) p.set("src", [...effectiveSources()].join(","));
  else if (!state.meta && state.sourcesFromUrl) p.set("src", [...state.sources].join(","));
  if (state.q) p.set("q", state.q);
  if (state.selected) p.set("event", state.selected);
  history.replaceState(null, "", "#" + p.toString());
}

/* ---------- Data: live API, or a static export (GitHub Pages) ---------- */
// Paths are relative so the site also works under a sub-path like /tokyo-events/.
const STATIC = document.documentElement.hasAttribute("data-static");
let staticData;
function loadStatic() {
  staticData ??= fetch("data/events.json").then((r) => {
    if (!r.ok) throw new Error(`data/events.json: ${r.status}`);
    return r.json();
  });
  return staticData;
}

// Mirrors /api/events: every group carries a pre-merged variant per source
// combination, so picking the right one reproduces the server-side merge.
async function queryStatic(p) {
  const data = await loadStatic();
  const from = p.get("from"), to = p.get("to");
  const cats = new Set((p.get("categories") || "").split(",").filter(Boolean));
  const sources = new Set((p.get("sources") || "").split(",").filter(Boolean));
  const needle = (p.get("q") || "").toLowerCase();
  const inRange = (e) => e.start_date <= to && e.end_date >= from;
  const hasText = (e) => [e.title, e.summary, e.venue_name, e.area].some((v) => v && v.toLowerCase().includes(needle));
  const sourceFacets = {};
  const merged = [];
  for (const g of data.events) {
    // Like the API, search every source's own wording, not just the merged record.
    if (!inRange(g.variants[g.sources.join(",")]) || (needle && !Object.values(g.variants).some(hasText))) continue;
    for (const s of g.sources) sourceFacets[s] = (sourceFacets[s] || 0) + 1;
    const keep = sources.size ? g.sources.filter((s) => sources.has(s)) : g.sources;
    const ev = keep.length && g.variants[keep.join(",")];
    if (ev && inRange(ev)) merged.push(ev);
  }
  const facets = {};
  for (const e of merged) for (const c of e.categories) facets[c] = (facets[c] || 0) + 1;
  const events = cats.size ? merged.filter((e) => e.categories.some((c) => cats.has(c))) : merged;
  events.sort((a, b) => (a.date_approx - b.date_approx) || a.start_date.localeCompare(b.start_date)
    || a.end_date.localeCompare(b.end_date) || a.title.localeCompare(b.title));
  return { from, to, count: events.length, facets, sourceFacets, events };
}

async function fetchMeta() {
  if (STATIC) return { ...(await loadStatic()).meta, today: D.tokyoToday() };
  return (await fetch("api/meta")).json();
}
const cache = new Map();
let inflight;
async function api(path) {
  if (cache.has(path)) return cache.get(path);
  inflight?.abort();
  inflight = new AbortController();
  const res = await fetch(path, { signal: inflight.signal });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
  const json = await res.json();
  cache.set(path, json);
  if (cache.size > 40) cache.delete(cache.keys().next().value);
  return json;
}

/* ---------- Presentation helpers ---------- */
const catLabel = (slug) => state.meta?.categories.find((c) => c.slug === slug)?.label ?? slug;
const catOrder = (slug) => Object.keys(CATEGORY_COLORS).indexOf(slug);
const sourceMeta = (name) => state.meta?.sources.find((s) => s.name === name) || { name, label: name, short: name.slice(0, 2).toUpperCase(), color: "#64748b" };
const sourceBadge = (name) => { const s = sourceMeta(name); return `<span class="src-badge" style="--sc:${s.color}" title="${esc(s.label)}">${esc(s.short)}</span>`; };

function primaryCat(ev) {
  const cats = [...ev.categories].sort((a, b) => catOrder(a) - catOrder(b));
  return cats.find((c) => state.cats.has(c)) || cats[0] || "other";
}

function dateText(ev) {
  if (ev.date_approx) return ev.date_label || "Date TBC";
  const opts = { day: "numeric", month: "short" };
  if (ev.start_date === ev.end_date) return D.f(ev.start_date, { weekday: "short", ...opts });
  return `${D.f(ev.start_date, opts)} – ${D.f(ev.end_date, opts)}`;
}

function priceTag(ev) {
  const t = (ev.price_text || "").trim();
  if (!t) return "";
  if (/^free/i.test(t) || (ev.price_min === 0 && ev.price_max === 0)) return `<span class="tag free">Free</span>`;
  const short = t.replace(/\s*–\s*.*/, "+").replace(/&yen;/g, "¥");
  return `<span class="tag">${esc(short.length > 14 ? "Paid" : short)}</span>`;
}

function hiResImage(url) {
  return url ? url.replace(/-\d+x\d+(\.\w+)$/, "$1") : url;
}

function relTime(iso) {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - new Date(iso)) / 6e4);
  if (mins < 2) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const h = Math.round(mins / 60);
  if (h < 36) return `${h}h ago`;
  return `${Math.round(h / 24)} days ago`;
}

/* ---------- Rendering: controls ---------- */
function renderControls() {
  const idx = TAB_MODES.indexOf(state.mode);
  document.querySelectorAll("#mode-tabs button").forEach((b, i) => b.setAttribute("aria-selected", i === idx));
  $(".segmented-thumb").style.transform = `translateX(${Math.max(idx, 0) * 100}%)`;
  $(".segmented-thumb").style.opacity = idx < 0 ? 0 : 1;

  const r = range();
  $("#range-label").textContent = rangeLabel(r);
  $("#anchor-input").value = state.mode === "custom" ? r[0] : state.anchor;
  $("#custom-range").hidden = state.mode !== "custom";
  $("#from-input").value = r[0];
  $("#to-input").value = r[1];
  $("#today-btn").classList.toggle("is-today", r[0] <= state.today && state.today <= r[1]);
  if ($("#search").value !== state.q) $("#search").value = state.q;
  syncSearchClear();
}

const CHIPS_COLLAPSED = 8;
function syncSearchClear() {
  const has = $("#search").value.length > 0;
  $("#search-clear").hidden = !has;
  $(".search").classList.toggle("has-value", has);
}

function renderChips() {
  const facets = state.data?.facets || {};
  // Busiest types first; selected types always stay visible.
  const all = (state.meta?.categories || [])
    .filter((c) => facets[c.slug] || state.cats.has(c.slug))
    .sort((a, b) => (facets[b.slug] || 0) - (facets[a.slug] || 0) || catOrder(a.slug) - catOrder(b.slug));
  const hidden = state.chipsOpen ? [] : all.slice(CHIPS_COLLAPSED).filter((c) => !state.cats.has(c.slug));
  const cats = all.filter((c) => !hidden.includes(c));
  const more = hidden.length ? `<button class="chip more" data-more>+${hidden.length} more</button>`
    : all.length > CHIPS_COLLAPSED ? `<button class="chip more" data-more>Fewer</button>` : "";
  const html = cats.map((c) => `
    <button class="chip ${facets[c.slug] ? "" : "empty"}" style="--c:${CATEGORY_COLORS[c.slug]}" data-cat="${c.slug}" aria-pressed="${state.cats.has(c.slug)}">
      <span class="dot"></span>${esc(c.label)}<span class="n">${facets[c.slug] || 0}</span>
    </button>`).join("");
  $("#chips").innerHTML = html + more + (state.cats.size ? `<button class="chip clear" data-clear>Clear</button>` : "");
}

const allSourceNames = () => (state.meta?.sources || []).map((s) => s.name);
const defaultSources = () => new Set((state.meta?.sources || []).filter((s) => s.defaultOn !== false).map((s) => s.name));
const effectiveSources = () => (state.sources.size ? state.sources : new Set(allSourceNames()));
function sourcesAreDefault() {
  const a = effectiveSources(), b = defaultSources();
  return a.size === b.size && [...a].every((n) => b.has(n));
}
// Selection as sent to the API: empty means every source.
function normalizeSources(selected) {
  return selected.size === allSourceNames().length ? new Set() : selected;
}

function renderSources() {
  const srcs = state.meta?.sources || [];
  if (srcs.length < 2) { $("#sources").innerHTML = ""; return; }
  const facets = state.data?.sourceFacets || {};
  const all = !state.sources.size;
  $("#sources").innerHTML = `<span class="sources-label">Sources</span>` + srcs.map((s) => {
    const on = all || state.sources.has(s.name);
    return `<button class="source-pill" data-source="${esc(s.name)}" aria-pressed="${on}" style="--sc:${s.color}" title="${on ? "Hide" : "Show"} events from ${esc(s.label)}">
      <span class="src-badge">${esc(s.short)}</span>${esc(s.label)}<span class="n">${facets[s.name] || 0}</span></button>`;
  }).join("");
}

function toggleSource(name) {
  const selected = new Set(effectiveSources());
  selected.has(name) ? selected.delete(name) : selected.add(name);
  if (!selected.size) { toast("Keep at least one source selected"); return; }
  state.sources = normalizeSources(selected);
  update();
}

function renderDensity(events) {
  const [f, t] = range();
  const days = D.diff(f, t) + 1;
  const el = $("#density");
  if (days < 3 || days > 62) { el.innerHTML = ""; return; }
  const counts = new Array(days).fill(0);
  for (const ev of events) {
    if (ev.date_approx) continue;
    const s = Math.max(0, D.diff(f, ev.start_date));
    const e = Math.min(days - 1, D.diff(f, ev.end_date));
    for (let i = s; i <= e; i++) counts[i]++;
  }
  const max = Math.max(1, ...counts);
  const bars = counts.map((n, i) => {
    const day = D.add(f, i);
    const dow = D.dow(day);
    const cls = [day === state.today && "today", (dow === 0 || dow === 6) && "weekend"].filter(Boolean).join(" ");
    const label = D.f(day, { weekday: "short", day: "numeric", month: "short" });
    return `<button class="${cls}" data-day="${day}" aria-label="${label}: ${n} events">
      <span class="tip">${label} · ${n}</span><span class="bar" style="height:${Math.max(7, (n / max) * 100)}%"></span></button>`;
  }).join("");
  const fmt = { day: "numeric", month: "short" };
  el.innerHTML = `<div class="rail">${bars}</div>
    <div class="density-legend"><span>${D.f(f, fmt)}</span><span>Events per day · tap to zoom in</span><span>${D.f(t, fmt)}</span></div>`;
}

/* ---------- Rendering: list ---------- */
function visibleEvents() {
  let evs = state.data?.events || [];
  if (state.inView && map) {
    const b = map.getBounds();
    evs = evs.filter((e) => e.lat != null && b.contains([e.lng, e.lat]));
  }
  return evs;
}

function groupEvents(evs) {
  const [f] = range();
  const groups = new Map();
  const ongoing = [], tbc = [], noLoc = [];
  for (const ev of evs) {
    if (ev.date_approx) tbc.push(ev);
    else if (ev.start_date < f) ongoing.push(ev);
    else {
      const key = ev.start_date;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(ev);
    }
  }
  const out = [];
  const single = state.mode === "day";
  for (const [day, list] of [...groups.entries()].sort()) {
    const label = single ? "Starting" + (day === state.today ? " today" : "")
      : day === state.today ? "Today" : day === D.add(state.today, 1) ? "Tomorrow"
      : D.f(day, { weekday: "long", day: "numeric", month: "short" });
    out.push([label, list]);
  }
  if (ongoing.length) out.push([single ? "On all day" : "Already running", ongoing.sort((a, b) => a.end_date.localeCompare(b.end_date))]);
  if (tbc.length) out.push(["Dates to be confirmed", tbc]);
  return out;
}

function renderList() {
  const list = $("#list");
  const evs = visibleEvents();
  const total = state.data?.count ?? 0;
  $("#result-count").innerHTML = state.inView && evs.length !== total
    ? `<strong>${evs.length}</strong> of ${total} events in view`
    : `<strong>${total}</strong> event${total === 1 ? "" : "s"}`;

  if (!evs.length) {
    const filtered = state.cats.size || !sourcesAreDefault() || state.q;
    list.innerHTML = `<div class="empty-state"><div class="big">Nothing on${state.inView ? " here" : ""}</div>
      ${filtered ? "No events match these filters." : state.inView ? "Try zooming out or moving the map." : "No events found for this period."}
      ${filtered ? `<br><button class="pill-btn" data-reset>Reset filters</button>` : ""}</div>`;
    return;
  }

  const tpl = $("#tpl-card");
  const frag = document.createDocumentFragment();
  let i = 0;
  for (const [label, group] of groupEvents(evs)) {
    const h = document.createElement("div");
    h.className = "group-label";
    h.innerHTML = `<span>${esc(label)}</span><span>${group.length}</span>`;
    frag.append(h);
    for (const ev of group) {
      const node = tpl.content.firstElementChild.cloneNode(true);
      const cat = primaryCat(ev);
      node.dataset.id = ev.id;
      node.style.setProperty("--c", CATEGORY_COLORS[cat]);
      node.style.setProperty("--i", Math.min(i++, 20));
      const img = node.querySelector("img");
      if (ev.image) { img.src = ev.image; img.onerror = () => img.setAttribute("data-failed", ""); }
      else img.setAttribute("data-failed", "");
      const cats = ev.categories.sort((a, b) => catOrder(a) - catOrder(b)).slice(0, 2)
        .map((c) => `<span class="cat" style="--c:${CATEGORY_COLORS[c]}"><i></i>${esc(catLabel(c))}</span>`).join("");
      node.querySelector(".card-kicker").innerHTML = cats + priceTag(ev) + (ev.date_approx ? `<span class="tag tbc">TBC</span>` : "")
        + `<span class="src-stack" aria-label="Listed by ${esc(ev.sources.map((x) => x.label).join(", "))}">${ev.sources.map((x) => sourceBadge(x.name)).join("")}</span>`;
      node.querySelector(".card-title").textContent = ev.title;
      const where = ev.area || ev.venue_name || (ev.lat == null ? "Location TBA" : "");
      const time = ev.start_date === ev.end_date && ev.time_text ? ev.time_text : "";
      node.querySelector(".card-meta").innerHTML = [dateText(ev), time, where].filter(Boolean).map(esc).join('<span class="sep">·</span>');
      frag.append(node);
    }
  }
  list.replaceChildren(frag);
}

function showSkeleton() {
  if (state.data) return; // keep stale results visible while refreshing
  $("#list").innerHTML = Array.from({ length: 6 }, () =>
    `<div class="skeleton"><div class="t"></div><div class="l"><span style="width:40%"></span><span style="width:85%"></span><span style="width:60%"></span></div></div>`).join("");
}

/* ---------- Detail ---------- */
function icsFor(ev) {
  const d = (s) => s.replace(/-/g, "");
  const lines = [
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Tokyo Events Map//EN", "BEGIN:VEVENT",
    `UID:${ev.id}@tokyo-events-map`,
    `DTSTAMP:${new Date().toISOString().replace(/[-:]/g, "").slice(0, 15)}Z`,
    `DTSTART;VALUE=DATE:${d(ev.start_date)}`,
    `DTEND;VALUE=DATE:${d(D.add(ev.end_date, 1))}`,
    `SUMMARY:${ev.title.replace(/[,;]/g, "\\$&")}`,
    `LOCATION:${[ev.venue_name, ev.venue_address].filter(Boolean).join(", ").replace(/[,;]/g, "\\$&")}`,
    `URL:${ev.url}`,
    "END:VEVENT", "END:VCALENDAR",
  ];
  return URL.createObjectURL(new Blob([lines.join("\r\n")], { type: "text/calendar" }));
}

function findEvent(id) {
  // Merged events answer to the id of any of their source records.
  return state.data?.events.find((e) => e.id === id || e.ids?.includes(id));
}

function openDetail(id, { fly = true } = {}) {
  const ev = findEvent(id);
  if (!ev) return;
  id = ev.id;
  state.selected = id;
  writeHash();
  const cat = primaryCat(ev);
  const cats = ev.categories.map((c) =>
    `<span class="chip" style="--c:${CATEGORY_COLORS[c]}" aria-pressed="true"><span class="dot"></span>${esc(catLabel(c))}</span>`).join("");
  const longDate = ev.date_approx ? (ev.date_label || "Date to be confirmed")
    : ev.start_date === ev.end_date ? D.f(ev.start_date, { weekday: "long", day: "numeric", month: "long", year: "numeric" })
    : `${D.f(ev.start_date, { day: "numeric", month: "long" })} – ${D.f(ev.end_date, { day: "numeric", month: "long", year: "numeric" })}`;
  const daysLeft = !ev.date_approx && ev.start_date <= state.today ? D.diff(state.today, ev.end_date) : null;
  const dateSub = ev.date_approx ? "Exact dates not announced yet"
    : daysLeft === 0 ? "Last day today" : daysLeft != null && daysLeft > 0 ? `On now · ends in ${daysLeft} day${daysLeft > 1 ? "s" : ""}`
    : ev.start_date > state.today ? `Starts in ${D.diff(state.today, ev.start_date)} days` : "";
  const directions = ev.lat != null
    ? `https://www.google.com/maps/dir/?api=1&destination=${ev.lat},${ev.lng}`
    : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent([ev.venue_name, ev.area, "Tokyo"].filter(Boolean).join(", "))}`;

  $("#detail").innerHTML = `
    <div class="detail-hero" style="--c:${CATEGORY_COLORS[cat]}">
      ${ev.image ? `<img src="${esc(hiResImage(ev.image))}" alt="" onerror="if(this.src!=='${esc(ev.image)}'){this.src='${esc(ev.image)}'}else{this.remove()}" />` : ""}
    </div>
    <div class="detail-inner">
      <div class="detail-cats">${cats}${ev.date_approx ? `<span class="tag tbc">Dates TBC</span>` : ""}</div>
      <h2>${esc(ev.title)}</h2>
      <div class="facts">
        <div class="fact"><svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>
          <div><b>${esc(longDate)}</b>${dateSub ? `<small>${esc(dateSub)}</small>` : ""}</div></div>
        ${ev.time_text ? `<div class="fact"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg><div><b>${esc(ev.time_text)}</b></div></div>` : ""}
        ${ev.price_text ? `<div class="fact"><svg viewBox="0 0 24 24"><path d="M6 4l6 8 6-8M12 12v8M7 13h10M7 17h10"/></svg><div><b>${esc(ev.price_text)}</b></div></div>` : ""}
        <div class="fact"><svg viewBox="0 0 24 24"><path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/></svg>
          <div><b>${esc(ev.venue_name || ev.area || "Location to be announced")}</b>
          ${ev.venue_address ? `<small>${esc(ev.venue_address)}</small>` : ""}
          ${ev.station ? `<small>${esc(ev.station)}</small>` : ""}
          ${ev.geo_precision === "area" ? `<small>Pin shows the ${esc(ev.area)} area, not the exact venue</small>` : ""}</div></div>
      </div>
      ${ev.summary ? `<p class="summary">${esc(ev.summary)}</p>` : ""}
      <div class="actions">
        <a class="btn primary" href="${esc(ev.url)}" target="_blank" rel="noopener">Full details
          <svg viewBox="0 0 24 24"><path d="M7 17L17 7M9 7h8v8"/></svg></a>
        <a class="btn" href="${directions}" target="_blank" rel="noopener">
          <svg viewBox="0 0 24 24"><path d="M3 11l18-8-8 18-2-8z"/></svg>Directions</a>
        ${ev.date_approx ? "" : `<a class="btn" href="${icsFor(ev)}" download="${esc(ev.id.split(":")[1])}.ics">
          <svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18M12 13v5M9.5 15.5h5"/></svg>Calendar</a>`}
      </div>
      <div class="listed-by">
        <span class="listed-label">Listed by ${ev.sources.length > 1 ? `${ev.sources.length} sources` : ""}</span>
        ${ev.sources.map((x) => `<a class="listed-src" href="${esc(x.url)}" target="_blank" rel="noopener" style="--sc:${sourceMeta(x.name).color}">
          ${sourceBadge(x.name)}<span>${esc(x.label)}</span><svg viewBox="0 0 24 24"><path d="M7 17L17 7M9 7h8v8"/></svg></a>`).join("")}
      </div>
    </div>`;
  $("#view-list").hidden = true;
  $("#view-detail").hidden = false;
  $("#detail").scrollTop = 0;
  setSheet("half", true);
  highlightOnMap(id, true);
  if (fly && ev.lat != null) {
    map.easeTo({ center: [ev.lng, ev.lat], zoom: Math.max(map.getZoom(), ev.geo_precision === "area" ? 13.5 : 15), duration: 900 });
  }
}

function closeDetail() {
  if (!state.selected) return;
  highlightOnMap(state.selected, false);
  state.selected = null;
  writeHash();
  $("#view-detail").hidden = true;
  const lv = $("#view-list");
  lv.hidden = false;
  lv.classList.remove("returning"); void lv.offsetWidth; lv.classList.add("returning");
}

/* ---------- Map ---------- */
let map;
let hoverPopup;
let hovered = null;

function currentTheme() {
  const t = document.documentElement.dataset.theme;
  if (t) return t;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function toGeoJSON() {
  const features = [];
  for (const ev of state.data?.events || []) {
    if (ev.lat == null) continue;
    const cat = primaryCat(ev);
    features.push({
      type: "Feature",
      id: undefined,
      geometry: { type: "Point", coordinates: [ev.lng, ev.lat] },
      properties: { id: ev.id, title: ev.title, color: CATEGORY_COLORS[cat], approx: ev.geo_precision === "area" ? 1 : 0, when: dateText(ev), where: ev.venue_name || ev.area || "" },
    });
  }
  return { type: "FeatureCollection", features };
}

function setupLayers() {
  // English labels where the tiles have them.
  for (const layer of map.getStyle().layers) {
    if (layer.type === "symbol" && map.getLayoutProperty(layer.id, "text-field")) {
      map.setLayoutProperty(layer.id, "text-field", ["coalesce", ["get", "name:en"], ["get", "name_en"], ["get", "name:latin"], ["get", "name"]]);
    }
  }
  if (map.getSource("events")) return;
  const dark = currentTheme() === "dark";
  map.addSource("events", { type: "geojson", data: toGeoJSON(), cluster: true, clusterRadius: 42, clusterMaxZoom: 14, promoteId: "id" });
  map.addLayer({
    id: "clusters", type: "circle", source: "events", filter: ["has", "point_count"],
    paint: {
      "circle-color": dark ? "#f1f0ee" : "#16171b",
      "circle-radius": ["interpolate", ["linear"], ["get", "point_count"], 2, 15, 20, 21, 100, 30],
      "circle-stroke-width": 5,
      "circle-stroke-color": dark ? "rgba(241,240,238,0.22)" : "rgba(22,23,27,0.16)",
    },
  });
  map.addLayer({
    id: "cluster-count", type: "symbol", source: "events", filter: ["has", "point_count"],
    layout: { "text-field": ["get", "point_count_abbreviated"], "text-font": ["Noto Sans Bold"], "text-size": 12.5, "text-allow-overlap": true },
    paint: { "text-color": dark ? "#16171b" : "#ffffff" },
  });
  map.addLayer({
    id: "points", type: "circle", source: "events", filter: ["!", ["has", "point_count"]],
    paint: {
      "circle-color": ["case", ["==", ["get", "approx"], 1], dark ? "#17181d" : "#ffffff", ["get", "color"]],
      // "zoom" must be the outermost expression, so the state switch lives in each stop.
      "circle-radius": ["interpolate", ["linear"], ["zoom"],
        10, ["case", ["boolean", ["feature-state", "active"], false], 10, ["boolean", ["feature-state", "hover"], false], 8, 5.5],
        15, ["case", ["boolean", ["feature-state", "active"], false], 13, ["boolean", ["feature-state", "hover"], false], 11, 8]],
      "circle-stroke-width": ["case", ["==", ["get", "approx"], 1], 3, ["boolean", ["feature-state", "active"], false], 4, 2],
      "circle-stroke-color": ["case", ["==", ["get", "approx"], 1], ["get", "color"], dark ? "#0e0f12" : "#ffffff"],
      "circle-radius-transition": { duration: 250 },
    },
  });
  map.addLayer({
    id: "points-halo", type: "circle", source: "events", filter: ["!", ["has", "point_count"]],
    paint: {
      "circle-color": ["get", "color"],
      "circle-opacity": ["case", ["boolean", ["feature-state", "active"], false], 0.22, 0],
      "circle-radius": 24,
      "circle-blur": 0.4,
    },
  }, "points");
  if (state.selected) highlightOnMap(state.selected, true);
}

function refreshMapData() {
  map?.getSource("events")?.setData(toGeoJSON());
}

function highlightOnMap(id, on) {
  if (!map?.getSource("events")) return;
  try { map.setFeatureState({ source: "events", id }, { active: on }); } catch {}
}

function setHover(id) {
  if (hovered === id) return;
  if (hovered && map.getSource("events")) try { map.setFeatureState({ source: "events", id: hovered }, { hover: false }); } catch {}
  hovered = id;
  if (id && map.getSource("events")) try { map.setFeatureState({ source: "events", id }, { hover: true }); } catch {}
  document.querySelectorAll(".card.hl").forEach((c) => c.classList.remove("hl"));
  if (id) document.querySelector(`.card[data-id="${CSS.escape(id)}"]`)?.classList.add("hl");
}

function panelPadding() {
  if (innerWidth <= 760) return { top: 60, bottom: innerHeight * 0.5, left: 20, right: 20 };
  return { top: 40, bottom: 40, left: $("#panel").offsetWidth + 40, right: 70 };
}

function initMap() {
  map = new maplibregl.Map({
    container: "map",
    style: MAP_STYLES[currentTheme()],
    center: TOKYO.center,
    zoom: TOKYO.zoom,
    minZoom: 8,
    maxZoom: 18,
    attributionControl: { compact: true },
    padding: panelPadding(),
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
  map.on("style.load", setupLayers);
  // The base styles reference a few pattern sprites they don't ship; fill them with a blank pixel.
  map.on("styleimagemissing", (e) => {
    if (!map.hasImage(e.id)) map.addImage(e.id, { width: 1, height: 1, data: new Uint8Array(4) });
  });
  addEventListener("resize", () => map.setPadding(panelPadding()));

  hoverPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 14, maxWidth: "280px" });

  map.on("mousemove", "points", (e) => {
    const f = e.features[0];
    map.getCanvas().style.cursor = "pointer";
    setHover(f.properties.id);
    hoverPopup.setLngLat(f.geometry.coordinates)
      .setHTML(`<div class="pop-title">${esc(f.properties.title)}</div><div class="pop-sub">${esc(f.properties.when)}${f.properties.where ? " · " + esc(f.properties.where) : ""}</div>`)
      .addTo(map);
  });
  map.on("mouseleave", "points", () => { map.getCanvas().style.cursor = ""; setHover(null); hoverPopup.remove(); });
  map.on("mouseenter", "clusters", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "clusters", () => (map.getCanvas().style.cursor = ""));

  map.on("click", "clusters", async (e) => {
    const f = e.features[0];
    const src = map.getSource("events");
    const zoom = await src.getClusterExpansionZoom(f.properties.cluster_id);
    if (zoom > 14) {
      // Everything is at (nearly) the same spot: list it instead of zooming forever.
      const leaves = await src.getClusterLeaves(f.properties.cluster_id, 50, 0);
      return showStackPopup(f.geometry.coordinates, leaves);
    }
    map.easeTo({ center: f.geometry.coordinates, zoom: zoom + 0.3, duration: 600 });
  });

  map.on("click", "points", (e) => {
    const [x, y] = [e.point.x, e.point.y];
    const hits = map.queryRenderedFeatures([[x - 4, y - 4], [x + 4, y + 4]], { layers: ["points"] });
    const unique = [...new Map(hits.map((h) => [h.properties.id, h])).values()];
    hoverPopup.remove();
    if (unique.length > 1) return showStackPopup(e.features[0].geometry.coordinates, unique);
    openDetail(e.features[0].properties.id, { fly: false });
  });

  map.on("moveend", () => { if (state.inView) renderList(); });
  map.on("dragstart", () => { if ($("#panel").dataset.sheet === "full") setSheet("half"); });
}

let stackPopup;
function showStackPopup(coords, features) {
  stackPopup?.remove();
  const items = features.map((f) =>
    `<button data-open="${esc(f.properties.id)}" style="--c:${f.properties.color}"><i></i><span>${esc(f.properties.title)}</span></button>`).join("");
  stackPopup = new maplibregl.Popup({ offset: 14, maxWidth: "300px" })
    .setLngLat(coords)
    .setHTML(`<div class="pop-list"><div class="pop-list-head">${features.length} events here</div>${items}</div>`)
    .addTo(map);
  stackPopup.getElement().addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-open]");
    if (b) { stackPopup.remove(); openDetail(b.dataset.open, { fly: false }); }
  });
}

/* ---------- Mobile bottom sheet ---------- */
function setSheet(pos, onlyIfSmaller = false) {
  const panel = $("#panel");
  const order = ["peek", "half", "full"];
  if (onlyIfSmaller && order.indexOf(panel.dataset.sheet) >= order.indexOf(pos)) return;
  panel.dataset.sheet = pos;
}

function initSheet() {
  const panel = $("#panel");
  const handle = $("#sheet-handle");
  let startY = null, startT = 0, moved = false;
  const snaps = () => ({ peek: 190, half: innerHeight * 0.52, full: innerHeight * 0.88 });
  handle.addEventListener("pointerdown", (e) => {
    startY = e.clientY; moved = false;
    startT = panel.getBoundingClientRect().top;
    handle.setPointerCapture(e.pointerId);
    panel.classList.add("dragging");
  });
  handle.addEventListener("pointermove", (e) => {
    if (startY == null) return;
    const dy = e.clientY - startY;
    if (Math.abs(dy) > 4) moved = true;
    const visible = Math.min(innerHeight * 0.88, Math.max(120, innerHeight - (startT + dy)));
    panel.style.transform = `translateY(${innerHeight * 0.88 - visible}px)`;
  });
  handle.addEventListener("pointerup", (e) => {
    panel.classList.remove("dragging");
    panel.style.transform = "";
    if (startY == null) return;
    const order = ["peek", "half", "full"];
    if (!moved) {
      setSheet(order[(order.indexOf(panel.dataset.sheet) + 1) % 3]);
    } else {
      const visible = innerHeight - (startT + e.clientY - startY);
      const s = snaps();
      setSheet(Object.entries(s).sort((a, b) => Math.abs(a[1] - visible) - Math.abs(b[1] - visible))[0][0]);
    }
    startY = null;
  });
}

/* ---------- Main update loop ---------- */
let reqSeq = 0;
async function update() {
  renderControls();
  writeHash();
  const [f, t] = range();
  const p = new URLSearchParams({ from: f, to: t });
  if (state.cats.size) p.set("categories", [...state.cats].join(","));
  if (state.sources.size) p.set("sources", [...state.sources].join(","));
  if (state.q) p.set("q", state.q);
  const seq = ++reqSeq;
  showSkeleton();
  try {
    const data = STATIC ? await queryStatic(p) : await api(`api/events?${p}`);
    if (seq !== reqSeq) return;
    state.data = data;
  } catch (err) {
    if (err.name === "AbortError") return;
    toast(`Couldn't load events: ${err.message}`);
    return;
  }
  renderChips();
  renderSources();
  renderDensity(state.data.events);
  renderList();
  refreshMapData();
  if (state.selected) {
    if (findEvent(state.selected)) { $("#view-detail").hidden || highlightOnMap(state.selected, true); }
    else closeDetail();
  }
}

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (el.hidden = true), 4000);
}

async function loadMeta() {
  try {
    state.meta = await fetchMeta();
    state.today = state.meta.today || state.today;
    const srcs = state.meta.sources.filter((s) => s.count);
    const last = srcs.map((s) => s.lastSuccess).filter(Boolean).sort().pop();
    $("#freshness").innerHTML = state.meta.total
      ? `<span class="live-dot"></span>${srcs.length} source${srcs.length > 1 ? "s" : ""} · updated ${relTime(last)}`
      : `No events yet: run <code>python3 -m tokyo_events scrape</code>`;
  } catch {
    $("#freshness").textContent = "API unavailable";
  }
}

/* ---------- Wiring ---------- */
function bind() {
  $("#mode-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-mode]");
    if (!b || b.dataset.mode === state.mode) return;
    if (b.dataset.mode === "custom") { const [f, t] = range(); state.from = f; state.to = state.mode === "day" ? D.add(f, 6) : t; }
    else if (state.mode === "weekend") state.anchor = range()[0];
    else if (state.mode === "custom") state.anchor = range()[0];
    state.mode = b.dataset.mode;
    update();
  });
  $("#prev").addEventListener("click", () => shift(-1));
  $("#next").addEventListener("click", () => shift(1));
  $("#today-btn").addEventListener("click", () => {
    state.anchor = state.today;
    if (state.mode === "custom") { const span = D.diff(...range()); state.from = state.today; state.to = D.add(state.today, span); }
    update();
  });
  $("#anchor-input").addEventListener("change", (e) => {
    if (!D.valid(e.target.value)) return;
    if (state.mode === "custom") { const span = D.diff(...range()); state.from = e.target.value; state.to = D.add(e.target.value, span); }
    else state.anchor = e.target.value;
    update();
  });
  $("#nav-label").addEventListener("click", () => { try { $("#anchor-input").showPicker(); } catch {} });
  $("#from-input").addEventListener("change", (e) => { if (D.valid(e.target.value)) { state.from = e.target.value; if (state.to && state.to < state.from) state.to = state.from; update(); } });
  $("#to-input").addEventListener("change", (e) => { if (D.valid(e.target.value)) { state.to = e.target.value; if (state.from && state.from > state.to) state.from = state.to; update(); } });

  $("#density").addEventListener("click", (e) => {
    const b = e.target.closest("[data-day]");
    if (!b) return;
    state.mode = "day"; state.anchor = b.dataset.day; update();
  });

  let searchTimer;
  $("#search-clear").addEventListener("click", () => {
    clearTimeout(searchTimer);
    $("#search").value = "";
    syncSearchClear();
    $("#search").focus();
    if (state.q) { state.q = ""; update(); }
  });
  $("#search").addEventListener("input", (e) => {
    syncSearchClear();
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.q = e.target.value.trim(); update(); }, 220);
  });

  $("#chips").addEventListener("click", (e) => {
    const b = e.target.closest(".chip");
    if (!b) return;
    if (b.hasAttribute("data-more")) { state.chipsOpen = !state.chipsOpen; renderChips(); return; }
    if (b.hasAttribute("data-clear")) state.cats.clear();
    else state.cats.has(b.dataset.cat) ? state.cats.delete(b.dataset.cat) : state.cats.add(b.dataset.cat);
    update();
  });

  $("#sources").addEventListener("click", (e) => {
    const b = e.target.closest("[data-source]");
    if (b) toggleSource(b.dataset.source);
  });

  $("#in-view").addEventListener("change", (e) => { state.inView = e.target.checked; renderList(); });

  const list = $("#list");
  list.addEventListener("click", (e) => {
    if (e.target.closest("[data-reset]")) { state.cats.clear(); state.sources = normalizeSources(defaultSources()); state.q = ""; update(); return; }
    const card = e.target.closest(".card");
    if (card) openDetail(card.dataset.id);
  });
  list.addEventListener("keydown", (e) => {
    const card = e.target.closest(".card");
    if (card && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openDetail(card.dataset.id); }
  });
  list.addEventListener("pointerover", (e) => { const c = e.target.closest(".card"); if (c) setHover(c.dataset.id); });
  list.addEventListener("pointerleave", () => setHover(null));

  $("#back-btn").addEventListener("click", closeDetail);

  $("#theme-btn").addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("te-theme", next); } catch {}
    map.setStyle(MAP_STYLES[next], { diff: false });
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!document.documentElement.dataset.theme) map.setStyle(MAP_STYLES[currentTheme()], { diff: false });
  });
  $("#locate-btn").addEventListener("click", () => map.flyTo({ ...TOKYO, duration: 1200 }));

  addEventListener("keydown", (e) => {
    const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName);
    if (e.key === "/" && !typing) { e.preventDefault(); $("#search").focus(); }
    else if (e.key === "Escape") {
      if (typing) document.activeElement.blur();
      else closeDetail();
    } else if (!typing && !e.metaKey && !e.ctrlKey && !e.altKey) {
      if (e.key === "ArrowLeft") shift(-1);
      else if (e.key === "ArrowRight") shift(1);
      else if ("dwsc".includes(e.key)) $(`[data-mode="${TAB_MODES["dwsc".indexOf(e.key)]}"]`).click();
    }
  });
}

async function main() {
  readHash();
  const wantedDetail = state.selected;
  state.selected = null;
  bind();
  initSheet();
  initMap();
  renderControls();
  await loadMeta();
  if (!state.sourcesFromUrl) state.sources = normalizeSources(defaultSources());
  if (!location.hash) state.anchor = state.today;
  await update();
  if (wantedDetail && findEvent(wantedDetail)) openDetail(wantedDetail);
}

main();
