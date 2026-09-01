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
  totals: { runs: number; empty_runs: number; distinct: number }
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

// Today in the LOCAL calendar, which is the frame asDate puts every label in.
// `new Date().toISOString().slice(0,10)` is the UTC date, and pairing it with a
// local-midnight parse reintroduced the off-by-one asDate exists to prevent —
// just on the "now" side instead of the label side. West of Greenwich, between
// evening and midnight, UTC is already tomorrow and every row read a day older;
// east of it, early in the morning, yesterday's row read "today".
const startOfToday = () => {
  const n = new Date()
  return new Date(n.getFullYear(), n.getMonth(), n.getDate())
}

// "2 days ago" answers "is this still happening?", which is the question a
// ranking by volume cannot answer on its own.
function ago(iso: string): string {
  const days = Math.round(
    (startOfToday().getTime() - asDate(iso).getTime())
    / 86_400_000)
  if (days <= 0) return "today"
  if (days === 1) return "yesterday"
  return `${days} days ago`
}

export default function TrafficPanel() {
  const [days, setDays] = useState(30)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [pages, setPages] = useState<PageRow[] | null>(null)
  const [searches, setSearches] = useState<Searches | null>(null)
  const [refs, setRefs] = useState<RefRow[] | null>(null)
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
        <Tile label="Pageviews" value={t.views} />
        <Tile label="Searches" value={t.searches} />
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
                  <td title={`first run ${longDate(s.first_seen)}`}>
                    {shortDate(s.last_seen)}
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
                  <td>{shortDate(s.last_seen)}
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

      <p className="admin-note">
        No address, user agent or account is stored with any of this. A visitor
        is a keyed hash that includes the calendar day, so the same person is
        one visitor today and an unrelated one tomorrow — which is why the dates
        above are days rather than times, and why visitor counts are never added
        across them. Rows are deleted after {summary.retention_days} days.
      </p>
    </>
  )
}

function Tile({ label, value, sub }: { label: string; value: number; sub?: string }) {
  return (
    <div className="admin-tile">
      <span className="admin-tile__value">{value.toLocaleString()}</span>
      <span className="admin-tile__label">{label}</span>
      {sub && <span className="admin-tile__sub">{sub}</span>}
    </div>
  )
}
