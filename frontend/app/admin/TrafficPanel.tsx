"use client"

import { useCallback, useEffect, useState } from "react"

// What the site is being used for. Owner-only on the server (see
// backend/api/traffic.py) — this component only decides what to draw.
//
// The one number to be careful with is "visitors". A visitor id is a per-DAY
// hash, deliberately, so the same person is a different visitor tomorrow and
// cannot be followed across weeks. That makes the daily figure real and a
// summed one meaningless, so nothing here adds them up: the header shows the
// busiest single day and says so.

interface Day { day: string; views: number; searches: number; visitors: number }
interface Summary {
  days: Day[]
  totals: { views: number; searches: number; busiest_day_visitors: number }
  retention_days: number
  enabled: boolean
}
// `label` is the story, series or hub name the path resolves to, and is absent
// for paths that are already readable (/library) or whose id no longer resolves.
// The path always comes too — it is what the row links to.
interface PageRow { path: string; label?: string | null; views: number; visitors: number }
interface SearchRow { query: string; runs: number; visitors: number; results: number | null }
interface EmptyRow { query: string; runs: number }
interface RefRow { host: string; hits: number; visitors: number }

const RANGES = [7, 30, 90]

export default function TrafficPanel() {
  const [days, setDays] = useState(30)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [pages, setPages] = useState<PageRow[] | null>(null)
  const [searches, setSearches] = useState<{ top: SearchRow[]; empty: EmptyRow[] } | null>(null)
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

  const peak = Math.max(1, ...summary.days.map(d => d.views))
  const nothing = summary.totals.views === 0 && summary.totals.searches === 0

  return (
    <>
      <h1 className="settings-title">Traffic</h1>

      <div className="admin-tabs">
        {RANGES.map(d => (
          <button key={d} className={`library-tab ${days === d ? "library-tab--on" : ""}`}
            onClick={() => setDays(d)}>Last {d} days</button>
        ))}
      </div>

      {!summary.enabled && (
        <p className="admin-note admin-warn">
          Recording is switched off (TRACKING=false), so nothing new is arriving.
          Anything below is history.
        </p>
      )}

      <div className="admin-tiles">
        <Tile label="Pageviews" value={summary.totals.views} />
        <Tile label="Searches" value={summary.totals.searches} />
        <Tile label="Visitors, busiest day" value={summary.totals.busiest_day_visitors} />
      </div>

      {nothing ? (
        <p className="admin-note">
          Nothing recorded in this window yet. Pageviews are reported by the app
          itself, so they start from the moment this build went live — and
          readers who send Do Not Track, or who block the request, are never
          counted. Crawlers are not counted at all: they do not run the beacon.
        </p>
      ) : (
        <>
          <h2 className="admin-site__name">By day</h2>
          <div className="traffic-days">
            {summary.days.map(d => (
              <div key={d.day} className="traffic-day" title={
                `${d.day}: ${d.views} views, ${d.visitors} visitors, ${d.searches} searches`}>
                <div className="traffic-day__bar"
                     style={{ height: `${Math.round((d.views / peak) * 100)}%` }} />
                <span className="traffic-day__label">{d.day.slice(5)}</span>
              </div>
            ))}
          </div>
        </>
      )}

      <h2 className="admin-site__name">Searches people ran</h2>
      {searches?.top?.length ? (
        <table className="traffic-table">
          <thead><tr><th>Query</th><th>Runs</th><th>Found</th></tr></thead>
          <tbody>
            {searches.top.map(s => (
              <tr key={s.query}>
                <td className="traffic-table__q">{s.query}</td>
                <td>{s.runs}</td>
                {/* null means no exit recorded a count, which is not the same
                    as a search that found nothing — see _note_total. */}
                <td>{s.results == null ? "—" : s.results.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className="admin-note">No searches recorded in this window.</p>}

      {!!searches?.empty?.length && (
        <>
          <h2 className="admin-site__name">Searches that found nothing</h2>
          <p className="admin-note">
            The most direct answer there is to "what should be crawled next":
            each of these is a reader who left with nothing, and it names the gap
            exactly.
          </p>
          <table className="traffic-table">
            <thead><tr><th>Query</th><th>Runs</th></tr></thead>
            <tbody>
              {searches.empty.map(s => (
                <tr key={s.query}>
                  <td className="traffic-table__q">{s.query}</td>
                  <td>{s.runs}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h2 className="admin-site__name">Most-viewed pages</h2>
      {pages?.length ? (
        <table className="traffic-table">
          <thead><tr><th>Page</th><th>Views</th><th>Visitors</th></tr></thead>
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
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className="admin-note">No pageviews recorded in this window.</p>}

      <h2 className="admin-site__name">Where readers came from</h2>
      {refs?.length ? (
        <table className="traffic-table">
          <thead><tr><th>Site</th><th>Arrivals</th><th>Visitors</th></tr></thead>
          <tbody>
            {refs.map(r => (
              <tr key={r.host}>
                <td className="traffic-table__q">{r.host}</td>
                <td>{r.hits}</td><td>{r.visitors}</td>
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
        one visitor today and an unrelated one tomorrow. Rows are deleted after{" "}
        {summary.retention_days} days.
      </p>
    </>
  )
}

function Tile({ label, value }: { label: string; value: number }) {
  return (
    <div className="admin-tile">
      <span className="admin-tile__value">{value.toLocaleString()}</span>
      <span className="admin-tile__label">{label}</span>
    </div>
  )
}
