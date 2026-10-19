"use client"
import { useEffect, useState } from "react"
import { fetchWithTimeout } from "@/lib/net"

import { SITE_LABELS, watchIndexTotals, type IndexTotals } from "@/lib/api"

export default function IndexStatus() {
  const [open, setOpen] = useState(false)
  const [built, setBuilt] = useState<string | null>(null)
  const [totals, setTotals] = useState<IndexTotals | null>(null)

  useEffect(() => {
    return watchIndexTotals(setTotals)
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

  // The per-archive breakdown comes out of the SAME payload as the headline
  // figure, never from a second request: a second scan recomputed at its own
  // moment once printed a total directly above bars that added up to
  // something else (684 rows apart, measured). `stories` is the sum of `sites`
  // server-side — see `_with_sites` in backend/api/stats.py.
  const sites = Object.entries(totals?.sites ?? {}).sort((a, b) => b[1] - a[1])
  const siteTotal = sites.reduce((a, [, n]) => a + (n || 0), 0)
  const n = sites.length || 3

  // The button has to say WHAT is indexed. A rounded live total makes the
  // current index size clear without pinning the UI to a stale fixed figure.
  // Asking which archives are covered is the whole pitch.
  const label = totals
    ? `${n} archives · ${fmt(totals.stories)}`
    : `${n} archives`

  return (
    <div className="index-status">
      <button className="index-status__btn" onClick={() => setOpen(o => !o)}
        aria-expanded={open} aria-haspopup="dialog"
        title="Which archives FicAtlas searches">
        <span className="index-status__dot" aria-hidden="true" />
        {label}
      </button>

      {open && (
        <>
          <button className="index-status__backdrop" onClick={() => setOpen(false)} aria-label="Close" />
          <div className="ix" role="dialog" aria-label="What FicAtlas searches">
            <p className="ix__eyebrow">Every search covers</p>
            <p className="ix__headline">
              {totals
                ? <><strong>{totals.stories.toLocaleString()}</strong> stories from {n} archives</>
                : <>Three archives</>}
            </p>

            {/* One row per archive, full name first: the badges' "FF.net" is
                fine on a result card and too terse for the place that answers
                "what is in here". The bar is the share of the same total. */}
            {sites.length > 0 && (
              <ul className="ix__sites">
                {sites.map(([site, count]) => {
                  const pct = siteTotal ? (count / siteTotal) * 100 : 0
                  const info = ARCHIVES[site]
                  return (
                    <li key={site} className="ix__site">
                      <div className="ix__site-row">
                        <span className="ix__site-name">
                          <span className={`ix__swatch ix__swatch--${site}`} aria-hidden="true" />
                          {info?.name ?? SITE_LABELS[site] ?? site}
                        </span>
                        <span className="ix__site-n">{count.toLocaleString()}</span>
                      </div>
                      <div className="ix__meter" aria-hidden="true">
                        <div className={`ix__meter-fill ix__swatch--${site}`}
                          style={{ width: `${Math.max(pct, 0.8)}%` }} />
                      </div>
                      <p className="ix__site-note">
                        {pct < 1 ? "under 1%" : `${pct.toFixed(0)}%`}
                        {" · "}
                        {info?.closed ? info.closed : (() => {
                          const details = [
                            totals?.sites_updated_at?.[site]
                              ? `newest story added ${ago(totals.sites_updated_at[site])}`
                              : null,
                          ]
                          const crawled = totals?.sites_crawled_last_week?.[site]
                          if (crawled != null) {
                            details.push(crawled > 0
                              ? `${fmt(crawled)} works crawled this week`
                              : totals?.sites_crawled_at?.[site]
                                ? `last crawled ${ago(totals.sites_crawled_at[site])}`
                                : "no recent crawl recorded")
                          }
                          return details.filter(Boolean).join(" · ") || "freshness unavailable"
                        })()}
                      </p>
                    </li>
                  )
                })}
              </ul>
            )}

            {/* Proof the index is alive, in sentences rather than a grid of
                abbreviated labels. "At least" because updated_at is NULL for
                most rows, so that figure is a floor. */}
            {totals && (
              <ul className="ix__live">
                {totals.indexed_last_day != null && totals.indexed_last_day > 0 && (
                  <li><strong>+{fmt(totals.indexed_last_day)}</strong> stories added in the past day
                    {totals.indexed_last_hour ? <> ({totals.indexed_last_hour.toLocaleString()} this hour)</> : null}</li>
                )}
                {totals.updated_last_month != null && totals.updated_last_month > 0 && (
                  <li title="A floor: many works have no recorded update date">
                    At least <strong>{fmt(totals.updated_last_month)}</strong> updated by their authors this month
                  </li>
                )}
                {totals.checked_last_week != null && totals.checked_last_week > 0 && (
                  <li><strong>{fmt(totals.checked_last_week)}</strong> re-checked against their archive this week</li>
                )}
                <li><strong>{fmt(totals.total_words)}</strong> words in all</li>
              </ul>
            )}

            {built && (
              <p className="ix__build">
                app build {new Date(built).toLocaleString(undefined,
                  { dateStyle: "medium", timeStyle: "short" })}
              </p>
            )}
          </div>
        </>
      )}
    </div>
  )
}

/** The archives' own names, and the one fact about each a reader needs. */
const ARCHIVES: Record<string, { name: string; closed?: string }> = {
  ao3: { name: "Archive of Our Own (AO3)" },
  ffnet: { name: "FanFiction.net" },
  fictionalley: {
    name: "FictionAlley",
    // It shut down; its works are kept from archived copies. A freshness
    // line would imply a live source.
    closed: "the archive has closed; kept from archived copies",
  },
}
