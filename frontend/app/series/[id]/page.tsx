"use client"

import Link from "next/link"
import { use, useEffect, useState } from "react"
import SiteHeader from "../../SiteHeader"
import { describeError, type Failure } from "@/lib/errors"

// A series, in reading order.
//
// The research on how readers use AO3's series is unambiguous about what this
// page is for: it is where you go to find out what to read NEXT. So the order is
// the point, the numbers are always visible, and everything else — kudos, word
// counts, summaries — is subordinate to "which one comes after this one".
//
// The provenance line is not a disclaimer for its own sake. AO3 series are
// declared by the author; FanFiction.net and FictionAlley have no series field
// at all, so ours are read off titles and summaries. Someone about to spend
// 1.9M words on a five-book sequence deserves to know which of those they are
// looking at.
interface Work {
  id: string; title: string; author: string; site: string
  word_count: number; chapter_count: number; kudos: number
  position: number | null; is_hosted: boolean; status: string | null
  summary: string | null; url: string
}
interface Series {
  id: string; name: string; author: string | null; site: string
  source: string; confidence: number; work_count: number
  total_words: number; works: Work[]
}

const SITE_LABELS: Record<string, string> = {
  ao3: "AO3", ffnet: "FF.net", fictionalley: "FicAlley",
}

export default function SeriesPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const [data, setData] = useState<Series | null>(null)
  const [error, setError] = useState<Failure | null>(null)

  useEffect(() => {
    fetch(`/api/stories/series/${id}`)
      .then(async r => {
        if (!r.ok) throw describeError(null, r.status)
        return r.json()
      })
      .then(setData)
      .catch(e => setError(e?.kind ? e : describeError(e)))
  }, [id])

  if (error) return (
    <div className="page-prose">
      <SiteHeader />
      <h1>Not found</h1>
      <p>{error.message}</p>
      <p><Link href="/" className="card-btn card-btn--primary">Back to search</Link></p>
    </div>
  )
  if (!data) return (
    <div className="settings-shell"><SiteHeader /><p className="loading">Loading…</p></div>
  )

  const fmt = (n: number) => n >= 1000 ? `${Math.round(n / 1000)}k` : String(n)

  return (
    <div className="settings-shell">
      <SiteHeader />

      <h1 className="settings-title">{data.name}</h1>
      <p className="series-page__by">
        {data.author && (
          <>by <Link href={`/?author=${encodeURIComponent(data.author)}`}>{data.author}</Link> · </>
        )}
        {data.work_count} {data.work_count === 1 ? "work" : "works"} ·{" "}
        {data.total_words.toLocaleString()} words ·{" "}
        <span className="badge">{SITE_LABELS[data.site] ?? data.site}</span>
      </p>

      <p className={`series-page__source series-page__source--${data.source}`}>
        {data.source === "explicit"
          ? "This series is the author's own — the name and the order are theirs, taken from the archive."
          : data.source === "stated"
          ? "Assembled from what the author wrote in their summaries — “sequel to…”, “third in the…”. The grouping is theirs; the name is our shorthand for it."
          : "Grouped by FicAtlas. This archive has no series field, so these were matched on distinctive words in their titles and ordered by publication. It may be wrong."}
      </p>

      <ol className="series-page__list">
        {data.works.map(w => (
          <li key={w.id} className="series-page__item">
            <span className="series-page__n">{w.position ?? "•"}</span>
            <div className="series-page__body">
              <Link href={`/story/${w.id}`} className="series-page__title">{w.title}</Link>
              <p className="series-page__meta">
                {fmt(w.word_count)} words
                {w.chapter_count ? ` · ${w.chapter_count} ch` : ""}
                {w.kudos ? ` · ${w.kudos.toLocaleString()} kudos` : ""}
                {w.status === "complete" ? " · complete" : w.status === "in_progress" ? " · in progress" : ""}
                {w.is_hosted && <span className="series-page__read"> · readable here</span>}
              </p>
              {w.summary && (
                <p className="series-page__summary">
                  {w.summary.length > 240 ? w.summary.slice(0, 240) + "…" : w.summary}
                </p>
              )}
            </div>
          </li>
        ))}
      </ol>

      <p className="series-page__foot">
        <Link href={`/?author=${encodeURIComponent(data.author ?? "")}`}>
          Everything else by this author →
        </Link>
      </p>
    </div>
  )
}
