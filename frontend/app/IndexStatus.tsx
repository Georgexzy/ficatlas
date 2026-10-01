"use client"
import { useEffect, useState } from "react"
import { fetchWithTimeout } from "@/lib/net"

import { SITE_LABELS, getIndexTotals, type IndexTotals } from "@/lib/api"

export default function IndexStatus() {
  const [open, setOpen] = useState(false)
  const [built, setBuilt] = useState<string | null>(null)
  const [totals, setTotals] = useState<IndexTotals | null>(null)

  useEffect(() => {
    getIndexTotals().then(d => { if (d) setTotals(d) })
  }, [])

  useEffect(() => {
    if (!open) return
    // cache: "no-store" so this reports the BUILD THIS PAGE IS RUNNING, not
    // whatever the service worker has cached — the whole point is to tell a
    // stale bundle apart from a real bug.
    fetchWithTimeout("/build.json", { cache: "no-store" })
      .then(r => r.json()).then(d => setBuilt(d.built)).catch(() => {})
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false) }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open])

  // Was millions-only, so the index's 126 billion words rendered as "126305.5M".
  const fmt = (n: number) =>
      n >= 1_000_000_000_000 ? `${(n/1_000_000_000_000).toFixed(1)}T`
    : n >= 1_000_000_000     ? `${(n/1_000_000_000).toFixed(1)}B`
    : n >= 1_000_000         ? `${(n/1_000_000).toFixed(1)}M`
    : n >= 1_000             ? `${(n/1_000).toFixed(0)}k`
    : String(n)

  // "3 hours ago" reads better than a timestamp for freshness at a glance.
  const ago = (iso: string | null) => {
    if (!iso) return "never"
    const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
    if (mins < 1) return "just now"
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.round(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    return `${Math.round(hrs / 24)}d ago`
  }

  // The per-archive breakdown now comes out of the SAME payload as the headline
  // figure rather than from a second request to /api/stats/sites.
  //
  // That second request is the defect this replaces. It is a different scan of
  // the same table, recomputed on its own lock at its own moment, and the panel
  // rendered its total from one and its per-archive bars from the other — so
  // "Stories: 20,852,026" sat directly above a set of bars that added up to
  // something else. Measured on the live index: one breakdown 684 rows behind
  // the total, a fresher one 259 rows ahead of it, in the same response.
  //
  // A reader can add those bars up. They are the shape of the index, and showing
  // them is the reason to show them, so a panel that cannot make them agree
  // with its own total is visibly broken. One payload, one scan, and `stories`
  // is the sum of `sites` server-side — see `_with_sites` in backend/api/stats.py.
  //
  // Falls back to no breakdown at all when the first sites refresh has not
  // landed, rather than fetching a second number that might not match.
  const sites = Object.entries(totals?.sites ?? {})
  const siteTotal = sites.reduce((a, [, n]) => a + (n || 0), 0)
  const newestIndexed = Object.values(totals?.sites_updated_at ?? {})
    .sort()
    .pop()

  return (
    <div className="index-status">
      <button className="index-status__btn" onClick={() => setOpen(o => !o)}>
        <span className="index-status__dot" />
        {totals ? `${fmt(totals.stories)} indexed` : "Index"}
      </button>

      {open && (
        <>
          <button className="index-status__backdrop" onClick={() => setOpen(false)} aria-label="Close" />
          <div className="index-status__panel">
            <p className="index-status__heading">Index</p>

            {totals && (
              <dl className="index-status__totals">
                <div><dt>Stories</dt><dd>{totals.stories.toLocaleString()}</dd></div>
                <div><dt>Readable here</dt><dd>{totals.hosted.toLocaleString()}</dd></div>
                <div><dt>Words</dt><dd>{fmt(totals.total_words)}</dd></div>
                {/* What the background workers have actually added. The index
                    is not a static dump, and without this there is no sign of
                    that from the outside. */}
                {totals.indexed_last_hour != null && (
                  <div>
                    <dt>Added past hour</dt>
                    <dd className="index-status__live">
                      +{totals.indexed_last_hour.toLocaleString()}
                    </dd>
                  </div>
                )}
                {totals.indexed_last_day != null && totals.indexed_last_day > 0 && (
                  <div><dt>Added past 24h</dt><dd>+{fmt(totals.indexed_last_day)}</dd></div>
                )}
                {/* Freshness, which is a different claim from "added".
                    "Added past 24h" is what WE pulled in; this is how much of
                    the index is a living work an author is still writing.
                    Prefixed "at least" because updated_at is NULL for 64% of
                    rows, so the true figure is higher and this one is a floor —
                    quoting it bare would understate the index and read as a
                    precise measurement it is not. */}
                {totals.updated_last_month != null && totals.updated_last_month > 0 && (
                  <div>
                    <dt>Updated past 30d</dt>
                    <dd title="A floor: 64% of the index has no recorded update date">
                      at least {fmt(totals.updated_last_month)}
                    </dd>
                  </div>
                )}
                {totals.checked_last_week != null && totals.checked_last_week > 0 && (
                  <div>
                    <dt>Re-checked past 7d</dt>
                    <dd title="Works re-read from their source archive in the last week">
                      {fmt(totals.checked_last_week)}
                    </dd>
                  </div>
                )}
                {totals.dlp != null && totals.dlp > 0 && (
                  <div><dt>Dark Lord Potter picks</dt><dd>{totals.dlp.toLocaleString()}</dd></div>
                )}
                {totals.hpffa != null && totals.hpffa > 0 && (
                  <div><dt>HP FanFiction Archive</dt><dd>{totals.hpffa.toLocaleString()}</dd></div>
                )}
              </dl>
            )}

            {/* A bar per archive: the share each contributes is the thing worth
                seeing, and a column of raw numbers doesn't convey it. The shares
                are of the same total printed above, because both now come from
                one scan. */}
            {sites.length > 0 && (
            <div className="index-status__sites">
              {sites.map(([site, count]) => {
                const pct = siteTotal ? (count / siteTotal) * 100 : 0
                return (
                  <div key={site} className="index-status__site">
                    <div className="index-status__site-row">
                      <span className={`badge badge--site-${site}`}>{SITE_LABELS[site] ?? site}</span>
                      <span className="index-status__count">
                        {count.toLocaleString()}
                        <span className="index-status__pct">{pct.toFixed(0)}%</span>
                      </span>
                    </div>
                    <div className="index-status__meter">
                      <div className={`index-status__meter-fill index-status__meter-fill--${site}`}
                        style={{ width: `${Math.max(pct, 0.5)}%` }} />
                    </div>
                  </div>
                )
              })}
            </div>
            )}

            {built && (
              <p className="index-status__build">
                app build {new Date(built).toLocaleString(undefined,
                  { dateStyle: "medium", timeStyle: "short" })}
              </p>
            )}

            <p className="index-status__hint">
              {newestIndexed
                ? <>Last new story indexed <strong>{ago(newestIndexed)}</strong>. Updates to
                   tracked fandoms are picked up automatically.</>
                : <>Built from public archive releases, and extended from live fetches.</>}
            </p>
          </div>
        </>
      )}
    </div>
  )
}
