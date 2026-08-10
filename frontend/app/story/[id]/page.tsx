"use client"
import { useEffect, useState } from "react"
import { useParams } from "next/navigation"
import Link from "next/link"
import BackLink from "../../BackLink"
import { downloadStoryForOffline, isStoryOffline, deleteOfflineStory, getOfflineStory } from "@/lib/offline"
import { describeError, type Failure } from "@/lib/errors"
import OfflineLink from "@/app/OfflineLink"
import { storyLink, isSeedUrl } from "@/lib/storyLinks"
import SiteHeader from "@/app/SiteHeader"

const API_BASE = ""  // relative — handled by Next.js rewrite to backend

interface ChapterMeta { id: string; number: number; title?: string; word_count: number }
interface StoryDetail {
  id: string; site: string; url: string; title: string; author: string;
  author_url?: string; summary?: string; language: string; rating?: string;
  status: string; word_count: number; chapter_count: number;
  chapter_count_total?: number; kudos: number; hits: number; bookmarks: number;
  comments: number; fandoms: string[]; relationships: string[]; characters: string[];
  tags: string[]; warnings: string[]; categories: string[];
  published_at?: string; updated_at?: string;
  is_hosted: boolean; wayback_url?: string; cross_post_urls?: string[]; chapters: ChapterMeta[]
}

import { SITE_LABELS } from "@/lib/api"

// This used to be `status === "complete" ? "Complete" : "In Progress"`, which
// states "In Progress" for anything that is not explicitly finished. Most of
// the index is neither: the bulk dumps carry no completion data, so 5.3M FF.net
// and 8.5k FicAlley works are stored as `unknown` precisely so we stop claiming
// they are unfinished. A binary label put that claim straight back on the page.
const STATUS_LABEL: Record<string, string> = {
  complete: "Complete",
  in_progress: "In Progress",
  abandoned: "Abandoned",
  unknown: "Not stated",
}

export default function StoryPage() {
  const params = useParams()
  const id = params?.id as string
  const [story, setStory] = useState<StoryDetail | null>(null)
  const [error, setError] = useState<Failure | null>(null)
  // True when what is on screen came from the offline copy rather than the API.
  const [fromCache, setFromCache] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const [bookmarked, setBookmarked] = useState(false)
  const [importing, setImporting] = useState(false)
  const [similar, setSimilar] = useState<any[]>([])
  // Offline save state
  const [offlineSaved, setOfflineSaved] = useState(false)
  const [savingOffline, setSavingOffline] = useState(false)
  const [offlineProgress, setOfflineProgress] = useState<{ done: number; total: number } | null>(null)
  const [offlineMsg, setOfflineMsg] = useState<string | null>(null)

  // Loading the story.
  //
  // The offline fallback is the point of the rewrite. This page only ever asked
  // the API, so a story you had explicitly saved for offline reading showed
  // "Failed to fetch" on its own page the moment the network went — and since
  // this page is the only route to its chapters, saving a story offline did not
  // in practice make it readable offline. The service worker was serving the
  // shell correctly; the shell then refused to render.
  //
  // Also given a timeout. `setError(String(e))` on a bare rejection printed
  // `Failed to fetch` or, worse, an empty string from `r.statusText` on an HTTP/2
  // response — where statusText is always empty — so a 500 rendered a blank
  // error box.
  useEffect(() => {
    if (!id) return
    // Abort on unmount or when the id changes, so a stale response for a
    // previous story can never overwrite the current one mid-navigation.
    const ctl = new AbortController()
    const timer = setTimeout(() => ctl.abort(), 20_000)
    let done = false

    const fromOffline = async () => {
      const saved = await getOfflineStory(id)
      if (!saved || done) return false
      // Marked so the page can say the reader is looking at a saved copy rather
      // than silently presenting a stale one as current.
      setStory({
        ...(saved as any),
        chapter_count: saved.chapters.length,
        chapters: saved.chapters.map(c => ({
          id: `${saved.id}-${c.number}`, number: c.number, title: c.title, word_count: 0,
        })),
        fandoms: saved.fandoms ?? [], relationships: [], characters: [], tags: [],
        warnings: [], categories: [],
        status: "unknown", language: "English", is_hosted: true,
        kudos: 0, hits: 0, bookmarks: 0, comments: 0,
        word_count: saved.word_count ?? 0,
      } as StoryDetail)
      setFromCache(true)
      return true
    }

    ;(async () => {
      try {
        const r = await fetch(`${API_BASE}/api/stories/${id}`, { signal: ctl.signal })
        if (!r.ok) throw describeError(null, r.status)
        const data = await r.json()
        if (done) return
        setStory(data)
        setFromCache(false)
      } catch (e: any) {
        if (done) return
        if (await fromOffline()) return
        if (done) return
        setError(e?.kind ? e : describeError(e))
      } finally {
        clearTimeout(timer)
      }
    })()

    // Fetch similar stories in parallel (non-blocking; failures are silent)
    fetch(`${API_BASE}/api/stories/${id}/similar?count=6`, { signal: ctl.signal })
      .then(r => r.ok ? r.json() : [])
      .then(d => setSimilar(Array.isArray(d) ? d : []))
      .catch(() => {})

    return () => { done = true; clearTimeout(timer); ctl.abort() }
  }, [id, reloadKey])

  // Coming back online should get the real record rather than leave the reader
  // on the saved copy.
  useEffect(() => {
    const retry = () => setReloadKey(k => k + 1)
    window.addEventListener("online", retry)
    return () => window.removeEventListener("online", retry)
  }, [])

  // Bookmark state from localStorage
  useEffect(() => {
    if (!story) return
    try {
      const list = JSON.parse(localStorage.getItem("ficatlas:bookmarks") ?? "[]")
      setBookmarked(list.some((b: any) => b.id === story.id))
    } catch {}
  }, [story])

  const toggleBookmark = () => {
    if (!story) return
    try {
      const list = JSON.parse(localStorage.getItem("ficatlas:bookmarks") ?? "[]")
      if (bookmarked) {
        localStorage.setItem("ficatlas:bookmarks", JSON.stringify(list.filter((b: any) => b.id !== story.id)))
        setBookmarked(false)
      } else {
        list.unshift({
          id: story.id, title: story.title, author: story.author,
          site: story.site, url: story.url, savedAt: new Date().toISOString(),
        })
        localStorage.setItem("ficatlas:bookmarks", JSON.stringify(list.slice(0, 200)))
        setBookmarked(true)
      }
    } catch {}
  }

  const importAndRead = async () => {
    if (!story || importing) return
    setImporting(true)
    try {
      const fd = new FormData(); fd.append("url", story.url)
      const r = await fetch(`${API_BASE}/api/library/import-url`, { method: "POST", body: fd })
      const data = await r.json()
      if (data.id) {
        window.location.href = `/story/${data.id}/chapter/1`
      } else {
        alert(`Import failed: ${data.error || data.detail || "unknown"}`)
        setImporting(false)
      }
    } catch (e: any) {
      alert(`Import failed: ${e.message || e}`)
      setImporting(false)
    }
  }

  // Check whether this story is already saved offline.
  useEffect(() => {
    if (id) isStoryOffline(id).then(setOfflineSaved).catch(() => {})
  }, [id])

  const saveOffline = async () => {
    if (!story || savingOffline) return
    setSavingOffline(true); setOfflineMsg(null); setOfflineProgress({ done: 0, total: story.chapter_count || 1 })
    try {
      const n = await downloadStoryForOffline(id, (done, total) => setOfflineProgress({ done, total }))
      setOfflineSaved(true)
      setOfflineMsg(`✓ Saved ${n} chapter${n === 1 ? "" : "s"} for offline reading.`)
    } catch (e: any) {
      setOfflineMsg(`Couldn't save offline: ${e.message || e}`)
    } finally {
      setSavingOffline(false); setOfflineProgress(null)
    }
  }

  const removeOffline = async () => {
    try {
      await deleteOfflineStory(id)
      setOfflineSaved(false)
      setOfflineMsg("Removed from offline storage.")
    } catch {}
  }

  // Series membership, asked for separately. Most works are in none, and the
  // join would be pure cost for them — so the page renders nothing until this
  // comes back with something.
  //
  // Declared ABOVE the early returns below. Both of those fire on the first
  // render, when story is still null, so hooks placed after them are never
  // reached — React's rule about unconditional hooks, and it fails silently
  // here: no error, just a box that never appears.
  const [series, setSeries] = useState<any[]>([])
  useEffect(() => {
    if (!story?.id) return
    fetch(`/api/stories/${story.id}/series`)
      .then(r => r.ok ? r.json() : null)
      .then(d => setSeries(d?.series ?? []))
      .catch(() => {})
  }, [story?.id])

  if (error) return (
    <div className="reader-shell"><SiteHeader />
      <BackLink fallback="/" fallbackLabel="Back to search" />
      <div className="reader-error" role="alert">
        <h1 className="reader-error__title">
          {error.kind === "offline" ? "Not saved on this device" : "Couldn't load this story"}
        </h1>
        <p className="reader-error__body">
          {error.kind === "offline"
            ? "You're offline and this story isn't saved here. Your saved stories are in your Library."
            : error.message}
        </p>
        <div className="reader-error__actions">
          {error.retryable && (
            <button className="btn btn--primary" onClick={() => setReloadKey(k => k + 1)}>Try again</button>
          )}
          <OfflineLink href="/library" className="btn btn--ghost">My library</OfflineLink>
        </div>
      </div>
    </div>
  )
  if (!story) return <div className="reader-shell"><SiteHeader />
      <BackLink fallback="/" fallbackLabel="Back to search" /><p className="loading">Loading…</p></div>


  return (
    <div className="reader-shell">
      <SiteHeader />
      <BackLink fallback="/" fallbackLabel="Back to search" />

      {/* Say so rather than presenting a saved copy as the live record. The tags,
          counts and chapter list below are whatever was true when it was saved,
          and the difference matters on a work still being updated. */}
      {fromCache && (
        <p className="story-detail__cached" role="status">
          Showing the copy saved on this device — the index couldn’t be reached.
        </p>
      )}

      <article className="story-detail">
        <header className="story-detail__header">
          <p className="story-detail__site">{SITE_LABELS[story.site] ?? story.site}</p>
          <h1 className="story-detail__title">{story.title}</h1>
          {/* The author's name searches OUR index — the whole point of the
              site is that it spans archives, and an AO3 user page only ever
              shows what they posted on AO3. The link out to their profile is
              kept as a separate arrow rather than being the primary action. */}
          {series.map(sr => (
            <div key={sr.id} className="series-box">
              <p className="series-box__head">
                <span className="series-box__name">{sr.name}</span>
                <span className="series-box__pos">
                  {sr.role === "side"
                    ? "side story"
                    : sr.position
                    ? `part ${sr.position}`
                    : `${sr.work_count} works`}
                </span>
              </p>
              {/* Says where the grouping came from. AO3 series are stated by the
                  author; FF.net and FictionAlley have no series field at all, so
                  ours are read off the titles — and a reader deciding what to
                  read next is entitled to know which of those they are looking
                  at, rather than being handed an order as though it were fact. */}
              {sr.source === "inferred" && (
                <p className="series-box__note">
                  Grouped by FicAtlas from the titles and publication order —{" "}
                  {sr.author ? <>all by <strong>{sr.author}</strong>. </> : null}
                  {sr.site === "ao3" ? "AO3 " : "This archive "}
                  did not publish a series list, so this is our reading of it.
                </p>
              )}
              {/* Every other entry is a link to that story, and it has to LOOK
                  like one. Underlined and accent-coloured rather than styled as
                  quiet body text: the whole reason someone opens a series list
                  is to go somewhere else in it, and a list that reads as a
                  static table of contents gets read and closed. */}
              <ol className="series-box__list">
                {sr.works.map((w: any) => (
                  <li key={w.id} className={`${w.is_current ? "is-current " : ""}${
                    w.role === "side" ? "is-side" : ""}`}>
                    <span className="series-box__n">
                      {w.role === "side" ? "◆" : w.position ?? "•"}
                    </span>
                    {w.is_current
                      ? <span className="series-box__this">
                          {w.title} <span className="series-box__here">you are here</span>
                        </span>
                      : <Link href={`/story/${w.id}`} className="series-box__link">
                          {w.title}<span className="series-box__go" aria-hidden="true">→</span>
                        </Link>}
                    <span className="series-box__meta">
                      {w.word_count ? `${Math.round(w.word_count / 1000)}k` : ""}
                      {w.kudos ? ` · ${w.kudos.toLocaleString()} kudos` : ""}
                    </span>
                  </li>
                ))}
              </ol>
              <p className="series-box__all">
                <Link href={`/series/${sr.id}`}>Open the full series →</Link>
              </p>
            </div>
          ))}

          <p className="story-detail__byline">
            by <Link href={`/?author=${encodeURIComponent(story.author)}`}
                 className="story-detail__author-link"
                 title={`All works by ${story.author}, across every archive`}>
              {story.author}
            </Link>
            {story.author_url && (
              <a href={story.author_url} target="_blank" rel="noopener noreferrer"
                className="story-detail__author-ext"
                title="Their profile on the original site">↗</a>
            )}
          </p>
          {story.fandoms.length > 0 && (
            <p className="story-detail__fandom">
              {story.fandoms.map((f, i) => (
                <span key={f}>
                  {i > 0 && " · "}
                  <Link href={`/?fandoms=${encodeURIComponent(f)}`}
                    className="story-detail__fandom-link">{f}</Link>
                </span>
              ))}
            </p>
          )}
        </header>

        <div className="story-detail__actions">
          {/* OfflineLink, not Link, on every route into the reader. Next's client
              router fetches an RSC payload before it will navigate, which fails
              with no connection — so these buttons did nothing offline, on the
              one page whose whole job offline is to get you into a saved story.

              "first" rather than a hard-coded chapter 1, and membership rather
              than `<= chapters.length`, because stored chapter numbers are not
              guaranteed contiguous and an offline copy holds only what actually
              downloaded. A story whose first chapter is numbered 0, or whose
              chapter 3 failed to save, was offered a link to a chapter that
              could not load. */}
          {story.is_hosted && story.chapters.length > 0 && (() => {
            const numbers = story.chapters.map(c => c.number).sort((a, b) => a - b)
            const first = numbers[0]
            let savedChapter = 0
            try {
              const p = JSON.parse(localStorage.getItem("ficatlas:progress") ?? "{}")
              savedChapter = p[story.id]?.chapter ?? 0
            } catch {}
            if (savedChapter !== first && numbers.includes(savedChapter)) {
              return (
                <>
                  <OfflineLink href={`/story/${story.id}/chapter/${savedChapter}`} className="btn btn--primary">
                    {`Continue Chapter ${savedChapter}`}
                  </OfflineLink>
                  <OfflineLink href={`/story/${story.id}/chapter/${first}`} className="btn btn--ghost">
                    Start over
                  </OfflineLink>
                </>
              )
            }
            return (
              <OfflineLink href={`/story/${story.id}/chapter/${first}`} className="btn btn--primary">
                {`Read Chapter ${first}`}
              </OfflineLink>
            )
          })()}
          {/* Seed rows are excluded: there is no real page for FicHub to fetch. */}
          {!story.is_hosted && !isSeedUrl(story.url)
            && (story.site === "ao3" || story.site === "ffnet") && (() => {
              const { href, label } = storyLink(story, SITE_LABELS)
              return (
                // The archive first, copying second. This had "Import & Read
                // here" as the primary action and the link to the archive as a
                // ghost button — offering to make a copy of a work that is
                // perfectly readable where its author put it, in preference to
                // sending the reader there. For a search engine that is both the
                // wrong emphasis and the wrong default: kudos, comments and
                // subscriptions only work at the source.
                <div className="btn-row">
                  <a href={href} target="_blank" rel="noopener noreferrer" className="btn btn--primary">
                    {label}
                  </a>
                  <button className="btn btn--ghost" onClick={importAndRead} disabled={importing}>
                    {importing ? "Importing…" : "Import & read here"}
                  </button>
                </div>
              )
            })()}
          {/* Reading in the app is the primary action for hosted works, but the
              original page stays reachable — this record IS a copy, and the
              source is part of its provenance. Hosted AO3/FFN works have no
              link out yet, so give them one. */}
          {story.is_hosted && !isSeedUrl(story.url)
            && (story.site === "ao3" || story.site === "ffnet") && (() => {
              const { href, label } = storyLink(story, SITE_LABELS)
              return (
                <a href={href} target="_blank" rel="noopener noreferrer" className="btn btn--ghost">
                  {label}
                </a>
              )
            })()}
          {/* Metadata-only seed rows have no page of their own, so they need this
              link even though they are filed under AO3. */}
          {!story.is_hosted
            && (isSeedUrl(story.url) || (story.site !== "ao3" && story.site !== "ffnet"))
            && (() => {
              const { href, label } = storyLink(story, SITE_LABELS)
              return (
                <a href={href} target="_blank" rel="noopener noreferrer" className="btn btn--primary">
                  {label}
                </a>
              )
            })()}
          <button className={`btn ${bookmarked ? "btn--on" : ""}`} onClick={toggleBookmark}>
            {bookmarked ? "★ Bookmarked" : "☆ Bookmark"}
          </button>
          {story.is_hosted && story.chapters.length > 0 && (
            <a href={`/api/stories/${story.id}/export.epub`}
              className="btn btn--ghost" download
              title="Download as EPUB for offline reading">
              ↓ EPUB
            </a>
          )}
          {story.is_hosted && story.chapters.length > 0 && (
            offlineSaved ? (
              <button className="btn btn--on" onClick={removeOffline}
                title="Saved on this device — tap to remove">
                ✓ Saved offline
              </button>
            ) : (
              <button className="btn btn--ghost" onClick={saveOffline} disabled={savingOffline}
                title="Save all chapters to this device for reading with no connection">
                {savingOffline
                  ? `Saving ${offlineProgress?.done ?? 0}/${offlineProgress?.total ?? "…"}…`
                  : "⤓ Save offline"}
              </button>
            )
          )}
          {/* For hosted FicAlley stories, expose Wayback alongside the in-app reader */}
          {story.is_hosted && story.site === "fictionalley" && (() => {
            let u = story.url
            if (u.includes("fictionalley.org") && !u.includes("fictionalley.org:")) {
              u = u.replace("fictionalley.org/", "fictionalley.org:80/")
            }
            return (
              <a href={`https://web.archive.org/web/2010/${u}`}
                target="_blank" rel="noopener noreferrer" className="btn btn--ghost">
                View on Wayback ↗
              </a>
            )
          })()}
          {story.wayback_url && (
            <a href={story.wayback_url} target="_blank" rel="noopener noreferrer" className="btn btn--ghost">
              Wayback ↗
            </a>
          )}
          {story.cross_post_urls?.map(url => {
            const kind = url.includes("archiveofourown.org") ? "AO3"
                       : url.includes("squidgeworld.org") ? "SquidgeWorld"
                       : url.includes("fanfiction.net") ? "FF.net"
                       : url.includes("fictionalley") ? "FictionAlley"
                       : url.startsWith("seed://") ? null
                       : "Mirror"
            if (!kind) return null
            return (
              <a key={url} href={url} target="_blank" rel="noopener noreferrer" className="btn btn--ghost">
                Also on {kind} ↗
              </a>
            )
          })}
        </div>

        {offlineMsg && (
          <p className="offline-msg">{offlineMsg}</p>
        )}

        {story.tags?.includes("dlp_library") && (
          <div className="dlp-banner">
            <span className="dlp-banner__icon">⚜</span>
            <span><strong>Recommended by Dark Lord Potter.</strong> A long-running Harry Potter community that reviews and rates stories; this one is on their recommended list, and their ratings are shown above.</span>
          </div>
        )}

        {story.summary && (
          <section className="story-detail__summary">
            <h3>Summary</h3>
            <p>{story.summary}</p>
          </section>
        )}

        <dl className="story-detail__meta">
          <div><dt>Words</dt><dd>{story.word_count.toLocaleString()}</dd></div>
          <div><dt>Chapters</dt><dd>{story.chapter_count}{story.chapter_count_total ? `/${story.chapter_count_total}` : "/?"}</dd></div>
          <div><dt>Status</dt><dd>{STATUS_LABEL[story.status] ?? "Not stated"}</dd></div>
          {story.rating && <div><dt>Rating</dt><dd>{story.rating}</dd></div>}
          {story.kudos > 0 && <div><dt>Kudos</dt><dd>{story.kudos.toLocaleString()}</dd></div>}
          {story.hits > 0 && <div><dt>Hits</dt><dd>{story.hits.toLocaleString()}</dd></div>}
          {story.updated_at && <div><dt>Updated</dt><dd>{story.updated_at.split("T")[0]}</dd></div>}
        </dl>

        {story.fandoms?.length > 0 && (
          <section className="story-detail__taggroup">
            <h4>Fandoms</h4>
            <div className="tag-list">{story.fandoms.map(f =>
              <Link key={f} href={`/?fandoms=${encodeURIComponent(f)}`} className="tag tag--fandom tag--clickable">{f}</Link>
            )}</div>
          </section>
        )}
        {story.relationships.length > 0 && (
          <section className="story-detail__taggroup">
            <h4>Relationships</h4>
            <div className="tag-list">{story.relationships.map(r =>
              <Link key={r} href={`/?relationships=${encodeURIComponent(r)}`} className="tag tag--ship tag--clickable">{r}</Link>
            )}</div>
          </section>
        )}
        {story.characters.length > 0 && (
          <section className="story-detail__taggroup">
            <h4>Characters</h4>
            <div className="tag-list">{story.characters.map(c =>
              <Link key={c} href={`/?characters=${encodeURIComponent(c)}`} className="tag tag--clickable">{c}</Link>
            )}</div>
          </section>
        )}
        {story.tags.length > 0 && (
          <section className="story-detail__taggroup">
            <h4>Tags</h4>
            <div className="tag-list">{story.tags.map(t =>
              <Link key={t} href={`/?tags=${encodeURIComponent(t)}`} className="tag tag--clickable">{t}</Link>
            )}</div>
          </section>
        )}
        {/* Where this record came from. Kept out of the tag list above: these are
            provenance, not content, and 61% of the index has nothing but these —
            rendering them as tags made untagged stories look tagged. */}
        {(story as any).sources?.length > 0 && (
          <p className="story-detail__source">
            Indexed from {(story as any).sources.join(" · ")}
            {story.tags.length === 0 && (
              <span className="story-detail__source-note">
                {" "}— this source provides no content tags
              </span>
            )}
          </p>
        )}
        {story.warnings?.filter(w => w !== "No Archive Warnings Apply").length > 0 && (
          <section className="story-detail__taggroup">
            <h4>Warnings</h4>
            <div className="tag-list">{story.warnings.filter(w => w !== "No Archive Warnings Apply").map(w =>
              <Link key={w} href={`/?tags=${encodeURIComponent(w)}`} className="tag tag--warn tag--clickable">{w}</Link>
            )}</div>
          </section>
        )}

        {story.is_hosted && story.chapters.length > 0 && (
          <section className="chapter-list">
            <h3>Chapters</h3>
            <ol>
              {story.chapters.map(ch => (
                <li key={ch.id}>
                  <OfflineLink href={`/story/${story.id}/chapter/${ch.number}`}>
                    <span className="chapter-list__num">{ch.number}.</span>
                    <span className="chapter-list__title">{ch.title || `Chapter ${ch.number}`}</span>
                    {/* Word counts are not kept in the offline copy, and
                        `ch.word_count.toLocaleString()` on the undefined that
                        left threw during render — blanking the entire page. */}
                    {ch.word_count > 0 && (
                      <span className="chapter-list__words">{ch.word_count.toLocaleString()} words</span>
                    )}
                  </OfflineLink>
                </li>
              ))}
            </ol>
          </section>
        )}

        {similar.length > 0 && (
          <section className="similar-stories">
            <h3>If you like this, try…</h3>
            <div className="similar-grid">
              {similar.map(s => (
                <Link key={s.id} href={`/story/${s.id}`} className="similar-card">
                  <span className="similar-card__title">{s.title}</span>
                  <span className="similar-card__author">by {s.author}</span>
                  <span className="similar-card__meta">
                    {s.word_count > 0 ? `${Math.round(s.word_count / 1000)}k words` : ""}
                    {s.relationships?.[0] ? ` · ${s.relationships[0]}` : ""}
                  </span>
                </Link>
              ))}
            </div>
          </section>
        )}
      </article>
    </div>
  )
}
