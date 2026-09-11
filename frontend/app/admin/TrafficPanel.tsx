"use client"

import { useCallback, useEffect, useMemo, useState } from "react"

// What the site is being used for. Owner-only on the server (see
// backend/api/traffic.py) — this component only decides what to draw.
//
// The one number to be careful with is "visitors". A visitor id is a per-DAY
// hash, deliberately, so the same person is a different visitor tomorrow and
// cannot be followed across weeks. That makes the daily figure real and a
// summed one meaningless, so nothing here adds them up: the header shows the
// busiest single day, names the date, and says so.

// ── Sorting, filtering and lifting rows out ─────────────────────────────────
//
// These tables answer a different question every time you open them. Server
// order is "most X first" for whichever X the endpoint chose, and that is the
// right default and the wrong answer to "which of the searches people ran found
// NOTHING", "which page has the worst visitors-per-view", or "when did that
// referrer last send anybody". The rows are already here — at most a couple of
// hundred, fetched in one go — so answering those is a comparator and not a
// round trip.
//
// Client-side deliberately. A server sort would need a parameter per column per
// endpoint, a re-fetch per click, and would still be capped by the same limit;
// this is instant, works offline once loaded, and cannot disagree with what is
// on screen.

type Dir = "asc" | "desc"

/** Compare two cell values of unknown type, nulls always last.
 *
 *  Nulls last in BOTH directions, which is not what a naive comparator does:
 *  `results: null` means no exit recorded a count, not "found zero", so a
 *  column sorted ascending must not open with a screenful of rows that have no
 *  value at all. Same rule the search sorts use — see nullslast() in
 *  api/search.py.
 */
function cmp(a: unknown, b: unknown, dir: Dir): number {
  const an = a == null || a === "", bn = b == null || b === ""
  if (an && bn) return 0
  if (an) return 1
  if (bn) return -1
  const s = typeof a === "number" && typeof b === "number"
    ? a - b
    // Dates arrive as ISO strings, which sort correctly as strings; everything
    // else is a query or a hostname, where a human ordering beats a byte one.
    : String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" })
  return dir === "asc" ? s : -s
}

/** Sort + filter for one table's rows.
 *
 *  `text` searches the fields named in `search`, because filtering on a column
 *  you cannot see is a puzzle: for a page row that is the path AND the resolved
 *  title, since either is what somebody would type.
 */
function useTable<T extends Record<string, any>>(
  rows: T[] | null | undefined, initial: keyof T & string,
  search: (keyof T & string)[], initialDir: Dir = "desc",
) {
  const [key, setKey] = useState<string>(initial)
  const [dir, setDir] = useState<Dir>(initialDir)
  const [text, setText] = useState("")
  // How many rows to draw. Thirty is right on a desktop, where the table is a
  // block you scan; on a phone four of these tables at thirty rows each made
  // the Traffic tab 8,130px — twelve screens — and the last table was somewhere
  // nobody has ever been. Eight is enough to see the shape and answer "what is
  // at the top", which is what these are open for; the rest is one tap away and
  // the count above says how many that is.
  const [cap, setCap] = useState<number | null>(null)
  useEffect(() => {
    try { setCap(window.matchMedia("(min-width: 700px)").matches ? null : 8) } catch {}
  }, [])
  const view = useMemo(() => {
    const all = rows ?? []
    const q = text.trim().toLowerCase()
    const kept = q
      ? all.filter(r => search.some(f => String(r[f] ?? "").toLowerCase().includes(q)))
      : all
    // Copy before sorting: the array belongs to the caller's state, and sorting
    // it in place mutates what React is holding.
    return [...kept].sort((a, b) => cmp(a[key], b[key], dir))
  }, [rows, key, dir, text, search])
  // Clicking the column you are already on reverses it; a new column starts
  // descending, because every column here is a count or a date and "most" or
  // "latest" first is what anybody means the first time they click.
  const sort = (k: string) => {
    if (k === key) setDir(d => (d === "asc" ? "desc" : "asc"))
    else { setKey(k); setDir("desc") }
  }
  const capped = cap == null ? view : view.slice(0, cap)
  return {
    view: capped, key, dir, sort, text, setText,
    total: (rows ?? []).length,
    // What the tools row needs to say "showing 8 of 30" honestly: `matched` is
    // after the filter and before the cap, so the two numbers describe the same
    // set the reader is looking at.
    matched: view.length,
    hiddenByCap: Math.max(0, view.length - capped.length),
    showAll: () => setCap(null),
  }
}

/** A sortable column heading. A real button, so it is reachable by keyboard and
 *  announced as one; `aria-sort` on the th is what a screen reader reads. */
function Th({ id, label, table, align }:
  { id: string; label: string; table: ReturnType<typeof useTable<any>>; align?: "r" }) {
  const on = table.key === id
  return (
    <th aria-sort={on ? (table.dir === "asc" ? "ascending" : "descending") : "none"}
      className={align === "r" ? "traffic-th--r" : undefined}>
      <button type="button" className={"traffic-sort" + (on ? " traffic-sort--on" : "")}
        onClick={() => table.sort(id)}>
        {label}<span className="traffic-sort__caret" aria-hidden="true">
          {on ? (table.dir === "asc" ? "\u2191" : "\u2193") : "\u2195"}</span>
      </button>
    </th>
  )
}

/** The row above a table: filter, how many rows are showing, and a way to take
 *  the numbers somewhere else.
 *
 *  Copying as TSV rather than offering a CSV download: the destination for
 *  these is a spreadsheet or a message to somebody, both of which take a paste,
 *  and a clipboard write needs no file, no filename and no cleanup. It copies
 *  exactly what is on screen — current filter, current sort — because a copy
 *  that quietly differs from the table above it is worse than no copy.
 */
function TableTools({ table, columns, rows }: {
  table: ReturnType<typeof useTable<any>>
  columns: { id: string; label: string }[]
  rows: Record<string, any>[]
}) {
  const [copied, setCopied] = useState<"" | "yes" | "no">("")
  const copy = async () => {
    const head = columns.map(c => c.label).join("\t")
    const body = rows.map(r => columns.map(c => {
      const v = r[c.id]
      return v == null ? "" : String(v).replace(/[\t\n]+/g, " ")
    }).join("\t")).join("\n")
    const text = `${head}\n${body}`
    // Two paths, because the admin page is opened over BOTH.
    //
    // navigator.clipboard does not exist outside a secure context, and the dev
    // host is plain http over the tailnet — so on the machine this site is
    // actually administered from, the modern API is simply undefined and the
    // button did nothing at all, silently. The textarea + execCommand fallback
    // is deprecated and works everywhere, which between them is the whole
    // argument for keeping it.
    let done = false
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
        done = true
      }
    } catch { /* fall through to the old way */ }
    if (!done) {
      try {
        const ta = document.createElement("textarea")
        ta.value = text
        // Off-screen rather than hidden: a display:none textarea cannot be
        // selected, and an unselected one copies nothing.
        ta.style.cssText = "position:fixed;top:-1000px;left:-1000px;opacity:0"
        document.body.appendChild(ta)
        ta.select()
        done = document.execCommand("copy")
        document.body.removeChild(ta)
      } catch { done = false }
    }
    // And SAY which happened. A copy button that reports success it did not
    // have is worse than one that admits it failed, because the failure is
    // discovered at the paste.
    setCopied(done ? "yes" : "no")
    setTimeout(() => setCopied(""), 1800)
  }
  const filtered = table.text.trim().length > 0
  return (
    <div className="traffic-tools">
      <input className="traffic-filter" type="search" value={table.text}
        onChange={e => table.setText(e.target.value)}
        placeholder="Filter rows…" aria-label="Filter rows" />
      <span className="traffic-tools__count">
        {table.hiddenByCap > 0
          ? `${rows.length} of ${table.matched}`
          : filtered ? `${rows.length} of ${table.total}`
          : `${table.total} row${table.total === 1 ? "" : "s"}`}
      </span>
      {table.hiddenByCap > 0 && (
        <button type="button" className="traffic-tools__copy" onClick={table.showAll}>
          Show all {table.matched}
        </button>
      )}
      <button type="button" className="traffic-tools__copy" onClick={copy}
        title="Copy these rows, as they are sorted and filtered, as tab-separated text">
        {copied === "yes" ? "Copied" : copied === "no" ? "Cannot copy" : "Copy"}
      </button>
    </div>
  )
}

interface Day { day: string; views: number; searches: number; visitors: number }
interface Summary {
  days: Day[]
  range: { from: string; to: string; days: number }
  totals: {
    views: number; searches: number
    busiest_day_visitors: number; busiest_day: string | null
    active_days: number; bot_views: number; bot_searches: number
    script_searches: number; script_visitors: number
  }
  previous?: { from: string; to: string; views: number; searches: number; visitor_days: number }
  retention_days: number
  enabled: boolean
  funnel?: {
    searched: number; opened_a_story: number; read_it: number
    read_without_searching: number
    not_a_browser: number; read_it_since: string; read_it_partial: boolean
  }
}
// `label` is the story, series or hub name the path resolves to, and is absent
// for paths that are already readable (/library) or whose id no longer resolves.
// The path always comes too — it is what the row links to.
interface PageRow {
  path: string; label?: string | null; views: number; visitors: number
  first_seen: string; last_seen: string
}
interface SearchRow {
  query: string; runs: number; visitors: number; results: number | null
  first_seen: string; last_seen: string
}
interface EmptyRow { query: string; runs: number; last_seen: string }
interface RefRow {
  host: string; hits: number; visitors: number
  first_seen: string; last_seen: string
}
interface Searches {
  top: SearchRow[]; empty: EmptyRow[]
  totals: { runs: number; empty_runs: number; distinct: number; search_only?: number }
}

interface Routes {
  sources: { source: string; opens: number; people: number }[]
  hub_views: number
  searches: number
}

// What each route is, in the words an operator thinks in rather than the ones
// the query groups by.
const ROUTE_LABEL: Record<string, string> = {
  search:        "A search",
  hub:           "A fandom or pairing hub",
  hub_index:     "The hub index",
  another_story: "Another story page",
  home:          "The home page",
  entry:         "Straight in (no previous page)",
  other:         "Somewhere else on the site",
}

const RANGES = [7, 30, 90]

// Dates arrive as plain YYYY-MM-DD. Parsing them with `new Date(iso)` alone
// treats them as UTC midnight and then renders them locally, which puts anyone
// west of Greenwich a day behind on every label in this panel. Appending the
// time pins them to local midnight instead.
const asDate = (iso: string) => new Date(`${iso}T00:00:00`)
const fmt = (iso: string, o: Intl.DateTimeFormatOptions) =>
  asDate(iso).toLocaleDateString(undefined, o)
const shortDate = (iso: string) => fmt(iso, { day: "numeric", month: "short" })
const longDate = (iso: string) =>
  fmt(iso, { weekday: "short", day: "numeric", month: "short", year: "numeric" })

// Search rows carry a full instant rather than a bare day, because when a query
// was run is part of what it tells you (see the /searches docstring).
//
// Those instants arrive WITHOUT an offset -- `at` is `timestamp without time
// zone` written from utcnow(), so the server emits "2026-09-02T13:13:41". Left
// alone, JS parses a bare date-time as LOCAL, which silently shifts every
// search by the viewer's offset and would put a run from 00:30 UTC on the wrong
// day for anyone west of Greenwich. Appending Z states the frame the data is
// actually in; toLocaleString then renders it in the viewer's own zone, which
// is the one they can compare against their own memory of the day.
const asInstant = (iso: string) =>
  new Date(/([zZ]|[+-]\d\d:?\d\d)$/.test(iso) ? iso : `${iso}Z`)
const stamp = (iso: string) => asInstant(iso).toLocaleString(undefined, {
  day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
})
const longStamp = (iso: string) => asInstant(iso).toLocaleString(undefined, {
  weekday: "short", day: "numeric", month: "short", year: "numeric",
  hour: "2-digit", minute: "2-digit", second: "2-digit",
})

// "2 days ago" answers "is this still happening?", which is the question a
// ranking by volume cannot answer on its own.
//
// Both sides stay in the UTC frame, because that is the frame the DATA is in:
// visit_events.at is `timestamp without time zone` written from utcnow(), the
// day buckets are `min(at)::date` on a UTC session, and api/traffic.py builds
// its day series from utcnow().date(). asDate() then parses both at local
// midnight purely so the label renders on the right calendar day -- it is the
// same transform applied to both sides, so the subtraction is unaffected.
//
// Taking "today" from the LOCAL calendar instead looks more correct and is not:
// it compares a local day against UTC-bucketed rows, so a viewer in UTC+2 at
// 00:30 sees traffic from five minutes ago labelled "yesterday".
function ago(iso: string): string {
  // slice(0, 10) takes the UTC calendar day out of either shape this receives --
  // a bare "2026-09-02" from /pages, or a full instant from /searches. Both
  // sides of the subtraction stay UTC-bucketed, which is the invariant the
  // comment above depends on.
  const days = Math.round(
    (asDate(new Date().toISOString().slice(0, 10)).getTime()
     - asDate(iso.slice(0, 10)).getTime())
    / 86_400_000)
  if (days <= 0) return "today"
  if (days === 1) return "yesterday"
  return `${days} days ago`
}

interface CfDay { day: string; requests: number; bytes: number }
interface Cloudflare {
  configured: boolean
  reason?: string
  missing?: string[]
  error?: string
  fix?: string
  detail?: string
  days?: CfDay[]
  totals?: { requests: number; bytes: number; cache_hits: number
             server_errors: number; client_errors: number }
  cache_ratio?: number | null
  cache_breakdown?: { status: string; requests: number }[]
  countries?: { country: string; requests: number }[]
  statuses?: { status: number; requests: number }[]
  paths?: { path: string; requests: number }[]
}

// A change worth showing, or nothing. Percentages on tiny numbers are theatre:
// 3 -> 5 is not a 67% surge, and a traffic page that says it is will be
// disbelieved on the one occasion it matters.
function trend(now: number, before: number): string | undefined {
  if (!before && !now) return undefined
  if (!before) return `first ${now === 1 ? "one" : now.toLocaleString()} in this window`
  if (now + before < 20) return `was ${before.toLocaleString()}`
  const pct = Math.round(((now - before) / before) * 100)
  if (pct === 0) return `level with the previous ${"period"}`
  return `${pct > 0 ? "up" : "down"} ${Math.abs(pct)}% on ${before.toLocaleString()}`
}

const pct = (n: number, total: number) =>
  total ? `${n * 100 / total < 0.1 ? "<0.1" : (n * 100 / total).toFixed(1)}%` : "—"

// Cloudflare's cacheStatus values, in words. Only `hit` was actually answered
// without asking this server; `dynamic` means the response was never eligible
// for caching, which for HTML is the default until a cache rule says otherwise.
const CACHE_WORDS: Record<string, string> = {
  hit: "Answered from cache",
  miss: "Cacheable, but not held yet",
  dynamic: "Not cacheable",
  bypass: "Cache deliberately skipped",
  expired: "Held but stale, refetched",
  revalidated: "Held, checked, still good",
  none: "No caching applied",
  unknown: "Unclassified",
}

const bytes = (n: number) => {
  const u = ["B", "kB", "MB", "GB", "TB"]
  let i = 0, v = n
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${u[i]}`
}

export default function TrafficPanel() {
  const [days, setDays] = useState(30)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [pages, setPages] = useState<PageRow[] | null>(null)
  const [searches, setSearches] = useState<Searches | null>(null)
  const [refs, setRefs] = useState<RefRow[] | null>(null)
  const [routes, setRoutes] = useState<Routes | null>(null)
  const [cf, setCf] = useState<Cloudflare | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (d: number) => {
    setError(null)
    const get = async (path: string) => {
      const r = await fetch(`/api/traffic/${path}?days=${d}`, { credentials: "include" })
      if (!r.ok) throw new Error(`Could not load traffic (${r.status}).`)
      return r.json()
    }
    try {
      // In parallel: four small aggregates over the same window, and waiting for
      // them one after another would show the page filling in for no reason.
      const [s, p, q, rf, rt] = await Promise.all([
        get("summary"), get("pages"), get("searches"), get("referrers"),
        get("routes"),
      ])
      setSummary(s); setPages(p.pages); setSearches(q); setRefs(rf.referrers)
      setRoutes(rt)
    } catch (e: any) { setError(e.message) }

    // Deliberately NOT in the Promise.all above. This one leaves the building
    // to reach Cloudflare, and the rest of the page must not wait on it or
    // fail with it -- so it lands whenever it lands, and its own section says
    // what happened.
    setCf(null)
    try { setCf(await get("cloudflare")) }
    catch (e: any) { setCf({ configured: true, error: "Could not load", detail: e.message }) }
  }, [])

  useEffect(() => { load(days) }, [days, load])

  // One per table. The initial column is the one the endpoint already ordered
  // by, so the first render is exactly what it was before any of this existed.
  const tTop   = useTable<SearchRow>(searches?.top, "runs", ["query"])
  const tEmpty = useTable<EmptyRow>(searches?.empty, "runs", ["query"])
  const tPages = useTable<PageRow>(pages, "views", ["path", "label"])
  const tRefs  = useTable<RefRow>(refs, "hits", ["host"])

  if (error) return <p className="settings-save-error" role="alert">{error}</p>
  if (!summary) return <p className="loading">Reading traffic…</p>

  // One scale for both series, so the two bars in a day can be compared with
  // each other. Scaling them separately would make 3 searches as tall as 40
  // views and quietly turn the chart into two unrelated pictures.
  const peak = Math.max(1, ...summary.days.map(d => Math.max(d.views, d.searches)))
  const nothing = summary.totals.views === 0 && summary.totals.searches === 0
  const t = summary.totals
  const prev = summary.previous ?? { views: 0, searches: 0, visitor_days: 0, from: "", to: "" }

  // Every day is drawn, so at 90 days there are 90 labels and they collide.
  // Label roughly eight of them, always including the last, so the axis stays
  // readable at any range without the label set jumping about as data arrives.
  const step = Math.max(1, Math.ceil(summary.days.length / 8))
  const labelled = (i: number) =>
    i === summary.days.length - 1 || (summary.days.length - 1 - i) % step === 0

  return (
    <>
      <h1 className="settings-title">Traffic</h1>

      <div className="admin-tabs">
        {RANGES.map(d => (
          <button key={d} className={`library-tab ${days === d ? "library-tab--on" : ""}`}
            onClick={() => setDays(d)}>Last {d} days</button>
        ))}
      </div>

      {/* The window, spelled out. "Last 30 days" is a control rather than a
          record of what is on screen, and the two stop agreeing the moment
          anybody screenshots this or compares it with something else. */}
      <p className="traffic-range">
        {longDate(summary.range.from)} — {longDate(summary.range.to)}
        <span className="traffic-range__sub">
          {t.active_days} of {summary.range.days} days saw any traffic
        </span>
      </p>

      {!summary.enabled && (
        <p className="admin-note admin-warn">
          Recording is switched off (TRACKING=false), so nothing new is arriving.
          Anything below is history.
        </p>
      )}

      {/* What was held back, said out loud.
          A number quietly removed from a total is indistinguishable from one
          that was never there, and the person reading this page should see the
          size of the correction rather than take it on trust. These are
          sessions that ran a search and never rendered a page — the search page
          is what fires the pageview beacon, so they were scripts calling the
          API, not readers. The funnel has always excluded them; until now the
          headline counts did not, so the same sessions were scripts in one tile
          and an audience two along. */}
      {t.script_searches > 0 && (
        <p className="admin-note">
          {t.script_searches.toLocaleString()} search
          {t.script_searches === 1 ? "" : "es"} from{" "}
          {t.script_visitors.toLocaleString()}{" "}
          {t.script_visitors === 1 ? "session" : "sessions"} are excluded below:
          they never loaded a page, so they are something calling the API rather
          than somebody using the site. A reader whose privacy blocker eats the
          beacon would look the same here — every population measured so far has
          been testing from this repo.
        </p>
      )}

      <div className="admin-tiles">
        <Tile label="Pageviews" value={t.views} sub={trend(t.views, prev.views)} />
        <Tile label="Searches" value={t.searches} sub={trend(t.searches, prev.searches)} />
        <Tile label="Visitors, busiest day" value={t.busiest_day_visitors}
              sub={t.busiest_day ? longDate(t.busiest_day) : undefined} />
        {/* Crawlers are excluded from every other number on this page, but
            "nobody came" and "nobody but crawlers came" are different facts,
            and while the site is waiting to be indexed the second one is the
            encouraging one. */}
        <Tile label="Crawler requests" value={t.bot_views + t.bot_searches}
              sub={`${t.bot_searches.toLocaleString()} searches, ${t.bot_views.toLocaleString()} pages`} />
      </div>

      {nothing ? (
        <p className="admin-note">
          Nothing recorded in this window yet. Pageviews are reported by the app
          itself, so they start from the moment this build went live — and
          readers who send Do Not Track, or who block the request, are never
          counted. Crawlers do not run the beacon, so they are counted only
          where the server sees them itself, which is searches.
        </p>
      ) : (
        <>
          <h2 className="admin-site__name">
            By day
            <span className="traffic-legend">
              <span className="traffic-legend__key traffic-legend__key--views" /> pageviews
              <span className="traffic-legend__key traffic-legend__key--searches" /> searches
            </span>
          </h2>
          {/* Every day in the range is drawn, including the empty ones. Grouping
              by the days that happen to have rows drew a chart with no gaps in
              it — 77 searches over 4 days became 4 adjacent bars, which reads as
              a busy week rather than four scattered days in a quiet month. */}
          <div className="traffic-days">
            {summary.days.map((d, i) => (
              <div key={d.day} className="traffic-day" title={
                `${longDate(d.day)}\n${d.views} views · ${d.visitors} visitors · ${d.searches} searches`}>
                <div className="traffic-day__bars">
                  {d.views > 0 && (
                    <div className="traffic-day__bar"
                         style={{ height: `${(d.views / peak) * 100}%` }} />
                  )}
                  {d.searches > 0 && (
                    <div className="traffic-day__bar traffic-day__bar--searches"
                         style={{ height: `${(d.searches / peak) * 100}%` }} />
                  )}
                </div>
                {labelled(i) && (
                  <span className="traffic-day__label">{shortDate(d.day)}</span>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {/* The only question worth asking of a search engine. */}
      {summary?.funnel && (
        <>
          <h2 className="admin-site__name">Did it work?</h2>
          <p className="admin-note">
            Counted in people, not clicks: somebody who searched nine times and
            opened one work is one of each. Bots are excluded, and so are the
            sessions that searched without ever loading a page — there were{" "}
            {summary.funnel.not_a_browser.toLocaleString()} of those in this
            window, and they are scripts rather than readers. Leaving them in
            halves the number below and flatters nobody.
          </p>
          <div className="admin-tiles">
            <div className="admin-tile">
              <span className="admin-tile__value">{summary.funnel.searched.toLocaleString()}</span>
              <span className="admin-tile__label">Searched</span>
            </div>
            <div className="admin-tile">
              <span className="admin-tile__value">{summary.funnel.opened_a_story.toLocaleString()}</span>
              <span className="admin-tile__label">Opened a story</span>
              {summary.funnel.searched > 0 && (
                <span className="admin-tile__sub">
                  {Math.round(100 * summary.funnel.opened_a_story / summary.funnel.searched)}% of searchers
                </span>
              )}
            </div>
            <div className="admin-tile">
              <span className="admin-tile__value">{summary.funnel.read_it.toLocaleString()}</span>
              <span className="admin-tile__label">Went and read it</span>
              {summary.funnel.opened_a_story > 0 && (
                <span className="admin-tile__sub">
                  {Math.round(100 * summary.funnel.read_it / summary.funnel.opened_a_story)}% of those
                </span>
              )}
              {/* Only when it actually applies to the window on screen.
                  Printing the caveat unconditionally teaches the reader to
                  discount a figure that is, for most windows, complete. */}
              {summary.funnel.read_it_partial && (
                <span className="admin-tile__sub">
                  only counted since {summary.funnel.read_it_since}
                </span>
              )}
            </div>
          </div>

          {/* The other way in, and on this site the one that is growing.
              These three tiles are now genuinely nested — each step is a subset
              of the one before it — which they were not: "went and read it"
              used to count everybody with an outbound click, including readers
              who never touched the search box. Those people are real and they
              are the SEO route: every page Google sends a reader to is a hub,
              so arriving there, opening a work and leaving for the archive is
              a complete success that the funnel above cannot see. */}
          {summary.funnel.read_without_searching > 0 && (
            <p className="admin-note">
              A further{" "}
              <strong>{summary.funnel.read_without_searching.toLocaleString()}</strong>{" "}
              {summary.funnel.read_without_searching === 1 ? "person" : "people"}{" "}
              went off to the archive without ever running a search — they
              arrived on a fandom or pairing hub, most likely from a search
              engine, and found what they wanted there. That is the same result
              by a different route, and it is counted apart from the funnel
              rather than inside it so each step above stays a subset of the one
              before.
            </p>
          )}
        </>
      )}

      {/* Which front door is working.
          The site has two and they serve different people: the search box is
          the product, and the 11,196 hub pages are the SEO surface — every page
          Google currently sends a reader to is one of them. Nothing on this
          panel could compare them; it could say a hub was viewed and that a
          story was viewed, and nothing about one leading to the other.

          Measured on the event immediately before each story-page view, within
          thirty minutes, rather than on what a session contains. Session-level
          counting cannot tell "searched, then browsed a hub, then opened a
          story" from the reverse, and most people who open a story here have
          done both. */}
      {routes && routes.sources.length > 0 && (
        <>
          <h2 className="admin-site__name">How readers reach a story</h2>
          <p className="admin-note">
            The page each story view directly followed. Counted per view, with a
            thirty-minute cutoff — a story opened an hour after a hub view is a
            new visit, not a click, and crediting the hub for it would flatter
            the wrong door.
          </p>
          <table className="traffic-table">
              <thead>
              <tr>
                <th>Came from</th>
                <th className="num">Story opens</th>
                <th className="num">People</th>
                <th className="num">Share</th>
              </tr>
            </thead>
            <tbody>
                {routes.sources.map(r => {
                  const total = routes.sources.reduce((a, b) => a + b.opens, 0)
                  return (
                    <tr key={r.source}>
                      <td>{ROUTE_LABEL[r.source] ?? r.source}</td>
                      <td className="num">{r.opens.toLocaleString()}</td>
                      <td className="num">{r.people.toLocaleString()}</td>
                      <td className="num">
                        {total > 0 ? `${Math.round(100 * r.opens / total)}%` : "—"}
                      </td>
                    </tr>
                  )
                })}
            </tbody>
          </table>
          {/* The comparison worth drawing, spelled out rather than left to be
              inferred from two rows of a table. "People" is the column that
              matters for the hubs: they reach MORE distinct readers than search
              does off far fewer views, which is what a discovery surface for
              strangers should look like. */}
          <p className="admin-note">
            Hub pages were viewed{" "}
            <strong>{routes.hub_views.toLocaleString()}</strong> times and
            searches ran <strong>{routes.searches.toLocaleString()}</strong>{" "}
            times in this window. Compare the two rows above on{" "}
            <em>people</em> rather than opens: a reader who searches opens
            several stories in one sitting, while a hub tends to bring one new
            person to one story — which is what a page that greets strangers
            from a search engine is supposed to do.
          </p>
        </>
      )}

      <h2 className="admin-site__name">Searches people ran</h2>
      {/* "Searches" means BOTH kinds now, and the distinction matters when
          reading the numbers below.
          Until 2026-09-07 this counted only searches carrying typed text,
          because the middleware recorded a row `if q`. Every fandom hub, every
          ship hub and every fandom, character or tag clicked on a result card
          made a search with no text in it, and none of them were here —
          measured on 24h of origin logs, 22 of 38 searches. So the report was
          blindest to the way people actually use the site, and any figure taken
          from before that date is a count of TYPED searches only, not of
          searches. A filter-only row is shown in the search bar's own syntax
          (`fandom:Naruto complete`), which is what the reader had in front of
          them and pastes back in to run it again. */}
      {searches?.top?.length ? (
        <>
          {/* Totals over the whole window, not over the rows below — the list is
              capped, so adding up what is displayed answers a question about the
              top 30 queries while looking like an answer about the site. */}
          <p className="admin-note">
            {searches.totals.runs.toLocaleString()} searches over{" "}
            {searches.totals.distinct.toLocaleString()} distinct queries.{" "}
            {searches.totals.empty_runs > 0 && <>
              {searches.totals.empty_runs.toLocaleString()} of them
              ({Math.round((searches.totals.empty_runs / searches.totals.runs) * 100)}%)
              found nothing.
            </>}
          </p>
          {/* The doubt the user-agent check cannot answer. Pageviews come from
              the browser beacon, so a visitor that searched and never rendered
              a page was not a browser — a script, or a test session. Worth
              seeing beside the counts, because these numbers get quoted. */}
          {!!searches.totals.search_only && searches.totals.search_only > 0 && (
            <p className="admin-note">
              {searches.totals.search_only.toLocaleString()}
              {" "}({Math.round((searches.totals.search_only / searches.totals.runs) * 100)}%)
              came from visitors who never loaded a page — automated, most likely,
              and not caught by the user-agent check.
            </p>
          )}
          <TableTools table={tTop} rows={tTop.view}
            columns={[{ id: "query", label: "Query" }, { id: "runs", label: "Runs" },
                      { id: "visitors", label: "People" }, { id: "results", label: "Found" },
                      { id: "last_seen", label: "Last run" }]} />
          <table className="traffic-table">
            <thead><tr>
              <Th id="query" label="Query" table={tTop} />
              <Th id="runs" label="Runs" table={tTop} align="r" />
              <Th id="visitors" label="People" table={tTop} align="r" />
              <Th id="results" label="Found" table={tTop} align="r" />
              <Th id="last_seen" label="Last run" table={tTop} />
            </tr></thead>
            <tbody>
              {tTop.view.map(s => (
                <tr key={s.query}>
                  <td className="traffic-table__q">{s.query}</td>
                  <td>{s.runs}</td>
                  <td>{s.visitors}</td>
                  {/* null means no exit recorded a count, which is not the same
                      as a search that found nothing — see _note_total. */}
                  <td>{s.results == null ? "—" : s.results.toLocaleString()}</td>
                  <td title={`first run ${longStamp(s.first_seen)}\nlast run ${longStamp(s.last_seen)}`}>
                    <span className="traffic-table__abs">{stamp(s.last_seen)}</span>
                    <span className="traffic-table__ago">{ago(s.last_seen)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : <p className="admin-note">No searches recorded in this window.</p>}

      {!!searches?.empty?.length && (
        <>
          <h2 className="admin-site__name">Searches that found nothing</h2>
          <p className="admin-note">
            The most direct answer there is to "what should be crawled next":
            each of these is a reader who left with nothing, and it names the gap
            exactly. The date says whether it is still being asked.
          </p>
          <TableTools table={tEmpty} rows={tEmpty.view}
            columns={[{ id: "query", label: "Query" }, { id: "runs", label: "Runs" },
                      { id: "last_seen", label: "Last run" }]} />
          <table className="traffic-table">
            <thead><tr>
              <Th id="query" label="Query" table={tEmpty} />
              <Th id="runs" label="Runs" table={tEmpty} align="r" />
              <Th id="last_seen" label="Last run" table={tEmpty} />
            </tr></thead>
            <tbody>
              {tEmpty.view.map(s => (
                <tr key={s.query}>
                  <td className="traffic-table__q">{s.query}</td>
                  <td>{s.runs}</td>
                  <td title={`last run ${longStamp(s.last_seen)}`}>
                    <span className="traffic-table__abs">{stamp(s.last_seen)}</span>
                    <span className="traffic-table__ago">{ago(s.last_seen)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h2 className="admin-site__name">Most-viewed pages</h2>
      {pages?.length ? (
        <><TableTools table={tPages} rows={tPages.view}
            columns={[{ id: "label", label: "Page" }, { id: "path", label: "Path" },
                      { id: "views", label: "Views" }, { id: "visitors", label: "People" },
                      { id: "first_seen", label: "First seen" }, { id: "last_seen", label: "Last seen" }]} />
        <table className="traffic-table">
          <thead><tr>
            <Th id="label" label="Page" table={tPages} />
            <Th id="views" label="Views" table={tPages} align="r" />
            <Th id="visitors" label="People" table={tPages} align="r" />
            <Th id="first_seen" label="First seen" table={tPages} />
            <Th id="last_seen" label="Last seen" table={tPages} />
          </tr></thead>
          <tbody>
            {tPages.view.map(p => (
              <tr key={p.path}>
                {/* Title first, path underneath. A row reading
                    /story/4b15fe7e-…/chapter/58 told you nothing about what was
                    read; the path still has to be here, because it is what
                    identifies the row and where the link goes. */}
                <td className="traffic-table__q">
                  <a href={p.path} target="_blank" rel="noopener noreferrer">
                    {p.label || p.path}
                  </a>
                  {p.label && <span className="traffic-table__path">{p.path}</span>}
                </td>
                <td>{p.views}</td><td>{p.visitors}</td>
                <td>{shortDate(p.first_seen)}</td>
                <td><span className="traffic-table__abs">{shortDate(p.last_seen)}</span>
                  <span className="traffic-table__ago">{ago(p.last_seen)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table></>
      ) : <p className="admin-note">No pageviews recorded in this window.</p>}

      <h2 className="admin-site__name">Where readers came from</h2>
      {refs?.length ? (
        <><TableTools table={tRefs} rows={tRefs.view}
            columns={[{ id: "host", label: "Site" }, { id: "hits", label: "Arrivals" },
                      { id: "visitors", label: "People" }, { id: "first_seen", label: "First seen" },
                      { id: "last_seen", label: "Last seen" }]} />
        <table className="traffic-table">
          <thead><tr>
            <Th id="host" label="Site" table={tRefs} />
            <Th id="hits" label="Arrivals" table={tRefs} align="r" />
            <Th id="visitors" label="People" table={tRefs} align="r" />
            <Th id="first_seen" label="First seen" table={tRefs} />
            <Th id="last_seen" label="Last seen" table={tRefs} />
          </tr></thead>
          <tbody>
            {tRefs.view.map(r => (
              <tr key={r.host}>
                <td className="traffic-table__q">{r.host}</td>
                <td>{r.hits}</td><td>{r.visitors}</td>
                <td>{shortDate(r.first_seen)}</td>
                <td><span className="traffic-table__abs">{shortDate(r.last_seen)}</span>
                  <span className="traffic-table__ago">{ago(r.last_seen)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table></>
      ) : (
        <p className="admin-note">
          No external referrers yet. Only the host is ever stored, never the page
          somebody arrived from, and arrivals from inside the site are not
          counted at all.
        </p>
      )}

      {/* The half of the traffic this page otherwise cannot see.

          Everything above comes from a beacon the BROWSER sends, so it counts
          only pages a human's browser actually rendered — and crawlers do not
          run JavaScript. On a site whose growth depends on being indexed, "is
          anything crawling us?" is the question the rest of this page
          structurally cannot answer. Cloudflare already counts every request at
          the edge because it is the thing serving them. */}
      <h2 className="admin-site__name">At the edge, from Cloudflare</h2>
      {!cf ? (
        <p className="loading">Asking Cloudflare…</p>
      ) : !cf.configured ? (
        <p className="admin-note">
          Not connected — {cf.reason ?? "no credentials"}. Cloudflare counts every
          request that reaches the site, including the crawlers this page cannot
          see. Set <code>FICATLAS_CF_API_TOKEN</code> and{" "}
          <code>FICATLAS_CF_ZONE_ID</code> in <code>.env</code>, then restart.
          Read-only; it stores nothing new.
        </p>
      ) : cf.error ? (
        <p className="admin-note admin-warn">
          {cf.error}. {cf.fix ?? cf.detail}
          {cf.fix && cf.detail && (
            <span className="traffic-table__path">Cloudflare said: {cf.detail}</span>
          )}
        </p>
      ) : cf.totals ? (
        <>
          <div className="admin-tiles">
            <Tile label="Requests at the edge" value={cf.totals.requests}
                  sub="everything: pages, JSON, images, crawlers" />
            <Tile label="Served from cache" value={cf.totals.cache_hits}
                  display={cf.cache_ratio != null
                    ? `${(cf.cache_ratio * 100).toFixed(1)}%`
                    : "—"}
                  sub={`${cf.totals.cache_hits.toLocaleString()} answered without asking this server`} />
            <Tile label="Bandwidth" value={cf.totals.bytes}
                  display={bytes(cf.totals.bytes)}
                  sub="served through Cloudflare" />
            <Tile label="Failed requests" value={cf.totals.server_errors}
                  sub={`${cf.totals.client_errors.toLocaleString()} were 4xx — not found, or refused`} />
          </div>

          {/* The gap IS the finding. These two numbers measure different
              things and must never be added together. */}
          <p className="admin-note">
            Cloudflare saw <strong>{cf.totals.requests.toLocaleString()} requests</strong>{" "}
            while the beacon recorded <strong>{t.views.toLocaleString()} pageviews</strong>.
            They count different things and do not add up: the first is every file
            fetched by anyone including crawlers, the second is pages rendered by a
            human&rsquo;s browser. A gap that grows while pageviews stay flat is
            something crawling the site — which is what you want.
          </p>

          {cf.cache_breakdown?.length ? (
            <>
              <h3 className="admin-subhead">How the edge answered</h3>
              <table className="traffic-table">
                <thead><tr><th>Cloudflare said</th><th>Requests</th><th>Share</th></tr></thead>
                <tbody>
                  {cf.cache_breakdown.map(c => (
                    <tr key={c.status}>
                      <td className="traffic-table__q">
                        {CACHE_WORDS[c.status] ?? c.status}
                        <span className="traffic-table__path">{c.status}</span>
                      </td>
                      <td>{c.requests.toLocaleString()}</td>
                      <td>{pct(c.requests, cf.totals!.requests)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}

          {cf.paths?.length ? (
            <>
              {/* Crawler-visible, unlike the pageview table above. Story pages do
                  not appear here however heavily they are crawled, because each
                  one is a distinct path and this groups by exact path. */}
              <h3 className="admin-subhead">Most-requested single paths</h3>
              <table className="traffic-table">
                <thead><tr><th>Path</th><th>Requests</th><th>Share</th></tr></thead>
                <tbody>
                  {cf.paths.map(p => (
                    <tr key={p.path}>
                      <td className="traffic-table__q">{p.path}</td>
                      <td>{p.requests.toLocaleString()}</td>
                      <td>{pct(p.requests, cf.totals!.requests)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}

          {cf.countries?.length ? (
            <>
              <h3 className="admin-subhead">Where the requests came from</h3>
              <table className="traffic-table">
                <thead><tr><th>Country</th><th>Requests</th><th>Share</th></tr></thead>
                <tbody>
                  {cf.countries.map(c => (
                    <tr key={c.country}>
                      <td className="traffic-table__q">{c.country}</td>
                      <td>{c.requests.toLocaleString()}</td>
                      <td>{pct(c.requests, cf.totals!.requests)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
        </>
      ) : null}

      <p className="admin-note">
        No address, user agent or account is stored with any of this. A visitor
        is a keyed hash that includes the calendar day, so the same person is
        one visitor today and an unrelated one tomorrow — which is why the dates
        above are days rather than times, and why visitor counts are never added
        across them. Rows are deleted after {summary.retention_days} days. The
        Cloudflare figures are read from an account that already has them and
        add nothing to what is stored here.
      </p>
    </>
  )
}

function Tile({ label, value, sub, display }:
              { label: string; value: number; sub?: string; display?: string }) {
  return (
    <div className="admin-tile">
      {/* `display` for values whose readable form is not a plain count --
          8,129,390,899 is not a number anybody reads, "7.6 GB" is. */}
      <span className="admin-tile__value">{display ?? value.toLocaleString()}</span>
      <span className="admin-tile__label">{label}</span>
      {sub && <span className="admin-tile__sub">{sub}</span>}
    </div>
  )
}
