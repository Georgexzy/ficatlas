"use client"

import { useCallback, useEffect, useState } from "react"

// What the site is being used for. Owner-only on the server (see
// backend/api/traffic.py) — this component only decides what to draw.
//
// The one number to be careful with is "visitors". A visitor id is a per-DAY
// hash, deliberately, so the same person is a different visitor tomorrow and
// cannot be followed across weeks. That makes the daily figure real and a
// summed one meaningless, so nothing here adds them up: the header shows the
// busiest single day, names the date, and says so.

interface Day { day: string; views: number; searches: number; visitors: number }
interface Summary {
  days: Day[]
  range: { from: string; to: string; days: number }
  totals: {
    views: number; searches: number
    busiest_day_visitors: number; busiest_day: string | null
    active_days: number; bot_views: number; bot_searches: number
  }
  previous?: { from: string; to: string; views: number; searches: number; visitors: number }
  retention_days: number
  enabled: boolean
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
      const [s, p, q, rf] = await Promise.all([
        get("summary"), get("pages"), get("searches"), get("referrers"),
      ])
      setSummary(s); setPages(p.pages); setSearches(q); setRefs(rf.referrers)
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

  if (error) return <p className="settings-save-error" role="alert">{error}</p>
  if (!summary) return <p className="loading">Reading traffic…</p>

  // One scale for both series, so the two bars in a day can be compared with
  // each other. Scaling them separately would make 3 searches as tall as 40
  // views and quietly turn the chart into two unrelated pictures.
  const peak = Math.max(1, ...summary.days.map(d => Math.max(d.views, d.searches)))
  const nothing = summary.totals.views === 0 && summary.totals.searches === 0
  const t = summary.totals
  const prev = summary.previous ?? { views: 0, searches: 0, visitors: 0, from: "", to: "" }

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

      <h2 className="admin-site__name">Searches people ran</h2>
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
          <table className="traffic-table">
            <thead><tr>
              <th>Query</th><th>Runs</th><th>People</th><th>Found</th><th>Last run</th>
            </tr></thead>
            <tbody>
              {searches.top.map(s => (
                <tr key={s.query}>
                  <td className="traffic-table__q">{s.query}</td>
                  <td>{s.runs}</td>
                  <td>{s.visitors}</td>
                  {/* null means no exit recorded a count, which is not the same
                      as a search that found nothing — see _note_total. */}
                  <td>{s.results == null ? "—" : s.results.toLocaleString()}</td>
                  <td title={`first run ${longStamp(s.first_seen)}\nlast run ${longStamp(s.last_seen)}`}>
                    {stamp(s.last_seen)}
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
          <table className="traffic-table">
            <thead><tr><th>Query</th><th>Runs</th><th>Last run</th></tr></thead>
            <tbody>
              {searches.empty.map(s => (
                <tr key={s.query}>
                  <td className="traffic-table__q">{s.query}</td>
                  <td>{s.runs}</td>
                  <td title={`last run ${longStamp(s.last_seen)}`}>
                    {stamp(s.last_seen)}
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
        <table className="traffic-table">
          <thead><tr>
            <th>Page</th><th>Views</th><th>People</th><th>First seen</th><th>Last seen</th>
          </tr></thead>
          <tbody>
            {pages.map(p => (
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
                <td>{shortDate(p.last_seen)}
                  <span className="traffic-table__ago">{ago(p.last_seen)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className="admin-note">No pageviews recorded in this window.</p>}

      <h2 className="admin-site__name">Where readers came from</h2>
      {refs?.length ? (
        <table className="traffic-table">
          <thead><tr>
            <th>Site</th><th>Arrivals</th><th>People</th><th>First seen</th><th>Last seen</th>
          </tr></thead>
          <tbody>
            {refs.map(r => (
              <tr key={r.host}>
                <td className="traffic-table__q">{r.host}</td>
                <td>{r.hits}</td><td>{r.visitors}</td>
                <td>{shortDate(r.first_seen)}</td>
                <td>{shortDate(r.last_seen)}
                  <span className="traffic-table__ago">{ago(r.last_seen)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
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
