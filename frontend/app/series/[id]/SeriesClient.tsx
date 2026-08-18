"use client"

import Link from "next/link"
import BackLink from "../../BackLink"
import { use, useEffect, useState } from "react"
import SiteHeader from "../../SiteHeader"
import { describeError, fetchJson, isAbort, type Failure } from "@/lib/errors"

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
  /** "main" — part of the numbered run; "side" — a companion piece. */
  role: string
}
interface Series {
  id: string; name: string; author: string | null; site: string
  source: string; confidence: number; work_count: number
  total_words: number; main_count: number; works: Work[]
}

const SITE_LABELS: Record<string, string> = {
  ao3: "AO3", ffnet: "FF.net", fictionalley: "FictionAlley",
}

export default function SeriesClient({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const [data, setData] = useState<Series | null>(null)
  const [error, setError] = useState<Failure | null>(null)

  useEffect(() => {
    const ctl = new AbortController()
    setError(null)
    fetchJson<Series>(`/api/stories/series/${id}`, { signal: ctl.signal })
      .then(setData)
      .catch(e => { if (!isAbort(e)) setError(e?.kind ? e : describeError(e)) })
    return () => ctl.abort()
  }, [id])

  if (error) return (
    <div className="page-prose">
      <SiteHeader />
      <BackLink fallback="/" fallbackLabel="Back to search" />
      {/* "Not found" was the heading for every failure, including a timeout and
          a 500 — telling someone the series does not exist when in fact we could
          not ask. The classification already knows the difference. */}
      <h1>{error.kind === "notfound" ? "Not found" : "Couldn’t load this series"}</h1>
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
      <BackLink fallback="/" fallbackLabel="Back to search" />

      <h1 className="settings-title">{data.name}</h1>
      <p className="series-page__by">
        {data.author && (
          <>by <Link href={`/?author=${encodeURIComponent(data.author)}`}>{data.author}</Link> · </>
        )}
        {data.main_count} in the main sequence
        {data.work_count > data.main_count &&
          ` · ${data.work_count - data.main_count} side ${
            data.work_count - data.main_count === 1 ? "story" : "stories"}`} ·{" "}
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

      {/* Split, because a series is usually not a flat list. Reading the
          Dangerverse in the order the numbers imply would put a 1,843-word
          vignette between two 500,000-word novels. The main run is the thing
          you came for; the companions are worth having and are not a step in
          it. */}
      <h2 className="series-page__section">
        The main sequence
        <span>{data.main_count} {data.main_count === 1 ? "work" : "works"}, in order</span>
      </h2>
      <ol className="series-page__list">
        {data.works.filter(w => w.role !== "side").map(w => (
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

      {data.works.some(w => w.role === "side") && (
        <>
          <h2 className="series-page__section">
            Side stories &amp; companions
            <span>set in the same world — read in any order, or not at all</span>
          </h2>
          <ol className="series-page__list series-page__list--side">
            {data.works.filter(w => w.role === "side").map(w => (
              <li key={w.id} className="series-page__item">
                <span className="series-page__n">◆</span>
                <div className="series-page__body">
                  <Link href={`/story/${w.id}`} className="series-page__title">{w.title}</Link>
                  <p className="series-page__meta">
                    {fmt(w.word_count)} words
                    {w.chapter_count ? ` · ${w.chapter_count} ch` : ""}
                    {w.kudos ? ` · ${w.kudos.toLocaleString()} kudos` : ""}
                    {w.is_hosted && <span className="series-page__read"> · readable here</span>}
                  </p>
                  {w.summary && (
                    <p className="series-page__summary">
                      {w.summary.length > 200 ? w.summary.slice(0, 200) + "…" : w.summary}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </>
      )}

      <p className="series-page__foot">
        <Link href={`/?author=${encodeURIComponent(data.author ?? "")}`}>
          Everything else by this author →
        </Link>
      </p>
    </div>
  )
}
