"use client"
import { useEffect, useState } from "react"

const API_BASE = ""  // relative — handled by Next.js rewrite to backend

interface SiteStat { site: string; count: number; last_indexed: string | null }
interface Totals   {
  stories: number; hosted: number; total_words: number; dlp?: number; hpffa?: number
  /** Rows added by the background workers. As fresh as the 5-minute stats cache. */
  indexed_last_hour?: number
  indexed_last_day?: number
}

import { SITE_LABELS } from "@/lib/api"

export default function IndexStatus() {
  const [open, setOpen] = useState(false)
  const [sites, setSites] = useState<SiteStat[]>([])
  const [built, setBuilt] = useState<string | null>(null)
  const [totals, setTotals] = useState<Totals | null>(null)

  useEffect(() => {
    fetch(`${API_BASE}/api/stats/totals`).then(r => r.json()).then(setTotals).catch(() => {})
  }, [])

  useEffect(() => {
    if (!open) return
    fetch(`${API_BASE}/api/stats/sites`).then(r => r.json()).then(setSites).catch(() => {})
    // cache: "no-store" so this reports the BUILD THIS PAGE IS RUNNING, not
    // whatever the service worker has cached — the whole point is to tell a
    // stale bundle apart from a real bug.
    fetch("/build.json", { cache: "no-store" })
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

  const siteTotal = sites.reduce((a, s) => a + (s.count || 0), 0)
  const newestIndexed = sites
    .map(s => s.last_indexed)
    .filter(Boolean)
    .sort()
    .pop() as string | undefined

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
                {totals.dlp != null && totals.dlp > 0 && (
                  <div><dt>Dark Lord Potter picks</dt><dd>{totals.dlp.toLocaleString()}</dd></div>
                )}
                {totals.hpffa != null && totals.hpffa > 0 && (
                  <div><dt>HP FanFiction Archive</dt><dd>{totals.hpffa.toLocaleString()}</dd></div>
                )}
              </dl>
            )}

            {/* A bar per archive: the share each contributes is the thing worth
                seeing, and a column of raw numbers doesn't convey it. */}
            <div className="index-status__sites">
              {sites.map(s => {
                const pct = siteTotal ? (s.count / siteTotal) * 100 : 0
                return (
                  <div key={s.site} className="index-status__site">
                    <div className="index-status__site-row">
                      <span className={`badge badge--site-${s.site}`}>{SITE_LABELS[s.site] ?? s.site}</span>
                      <span className="index-status__count">
                        {s.count.toLocaleString()}
                        <span className="index-status__pct">{pct.toFixed(0)}%</span>
                      </span>
                    </div>
                    <div className="index-status__meter">
                      <div className={`index-status__meter-fill index-status__meter-fill--${s.site}`}
                        style={{ width: `${Math.max(pct, 0.5)}%` }} />
                    </div>
                  </div>
                )
              })}
            </div>

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
                : <>Built from public archive releases, and topped up from the archives as people search.</>}
            </p>
          </div>
        </>
      )}
    </div>
  )
}
