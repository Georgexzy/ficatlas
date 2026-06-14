"use client"
import { useEffect, useState } from "react"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (typeof window !== "undefined" ? `${window.location.protocol}//${window.location.hostname}:8000` : "http://localhost:8000")

interface SiteStat { site: string; count: number; last_indexed: string | null }
interface Totals   { stories: number; hosted: number; total_words: number }

const SITE_LABELS: Record<string, string> = {
  ao3: "AO3", ffnet: "FF.net", fictionalley: "FicAlley",
  royalroad: "Royal Road", spacebattles: "SpaceBattles",
}

export default function IndexStatus() {
  const [open, setOpen] = useState(false)
  const [sites, setSites] = useState<SiteStat[]>([])
  const [totals, setTotals] = useState<Totals | null>(null)

  useEffect(() => {
    fetch(`${API_BASE}/api/stats/totals`).then(r => r.json()).then(setTotals).catch(() => {})
  }, [])

  useEffect(() => {
    if (!open) return
    fetch(`${API_BASE}/api/stats/sites`).then(r => r.json()).then(setSites).catch(() => {})
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false) }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open])

  const fmt = (n: number) => n >= 1_000_000 ? `${(n/1_000_000).toFixed(1)}M`
                       : n >= 1000 ? `${(n/1000).toFixed(0)}k`
                       : String(n)

  return (
    <div className="index-status">
      <button className="index-status__btn" onClick={() => setOpen(o => !o)}>
        <span className="index-status__dot" />
        {totals ? `${fmt(totals.stories)} indexed` : "—"}
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
              </dl>
            )}

            <div className="index-status__sites">
              {sites.map(s => (
                <div key={s.site} className="index-status__site">
                  <span className={`badge badge--site-${s.site}`}>{SITE_LABELS[s.site] ?? s.site}</span>
                  <span className="index-status__count">{s.count.toLocaleString()}</span>
                </div>
              ))}
            </div>

            <p className="index-status__hint">
              Bulk-imported from official archives. Live results pulled on-demand.
            </p>
          </div>
        </>
      )}
    </div>
  )
}
