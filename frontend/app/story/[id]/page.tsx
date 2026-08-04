"use client"
import { useEffect, useState } from "react"
import { useParams } from "next/navigation"
import Link from "next/link"
import { downloadStoryForOffline, isStoryOffline, deleteOfflineStory } from "@/lib/offline"
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
  const [error, setError] = useState<string | null>(null)
  const [bookmarked, setBookmarked] = useState(false)
  const [importing, setImporting] = useState(false)
  const [similar, setSimilar] = useState<any[]>([])
  // Offline save state
  const [offlineSaved, setOfflineSaved] = useState(false)
  const [savingOffline, setSavingOffline] = useState(false)
  const [offlineProgress, setOfflineProgress] = useState<{ done: number; total: number } | null>(null)
  const [offlineMsg, setOfflineMsg] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    fetch(`${API_BASE}/api/stories/${id}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(setStory)
      .catch(e => setError(String(e)))
    // Fetch similar stories in parallel (non-blocking; failures are silent)
    fetch(`${API_BASE}/api/stories/${id}/similar?count=6`)
      .then(r => r.ok ? r.json() : [])
      .then(d => setSimilar(Array.isArray(d) ? d : []))
      .catch(() => {})
  }, [id])

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

  if (error) return <div className="reader-shell"><SiteHeader /><div className="alert alert--error">{error}</div></div>
  if (!story) return <div className="reader-shell"><SiteHeader /><p className="loading">Loading…</p></div>

  return (
    <div className="reader-shell">
      <SiteHeader />

      <article className="story-detail">
        <header className="story-detail__header">
          <p className="story-detail__site">{SITE_LABELS[story.site] ?? story.site}</p>
          <h1 className="story-detail__title">{story.title}</h1>
          {/* The author's name searches OUR index — the whole point of the
              site is that it spans archives, and an AO3 user page only ever
              shows what they posted on AO3. The link out to their profile is
              kept as a separate arrow rather than being the primary action. */}
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
          {story.is_hosted && story.chapters.length > 0 && (() => {
            let savedChapter = 0
            try {
              const p = JSON.parse(localStorage.getItem("ficatlas:progress") ?? "{}")
              savedChapter = p[story.id]?.chapter ?? 0
            } catch {}
            if (savedChapter > 1 && savedChapter <= story.chapters.length) {
              return (
                <>
                  <Link href={`/story/${story.id}/chapter/${savedChapter}`} className="btn btn--primary">
                    Continue Chapter {savedChapter}
                  </Link>
                  <Link href={`/story/${story.id}/chapter/1`} className="btn btn--ghost">
                    Start over
                  </Link>
                </>
              )
            }
            return (
              <Link href={`/story/${story.id}/chapter/1`} className="btn btn--primary">
                Read Chapter 1
              </Link>
            )
          })()}
          {/* Seed rows are excluded: there is no real page for FicHub to fetch. */}
          {!story.is_hosted && !isSeedUrl(story.url)
            && (story.site === "ao3" || story.site === "ffnet") && (
            <button className="btn btn--primary" onClick={importAndRead} disabled={importing}>
              {importing ? "Importing…" : "Import & Read here"}
            </button>
          )}
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
                  <Link href={`/story/${story.id}/chapter/${ch.number}`}>
                    <span className="chapter-list__num">{ch.number}.</span>
                    <span className="chapter-list__title">{ch.title || `Chapter ${ch.number}`}</span>
                    <span className="chapter-list__words">{ch.word_count.toLocaleString()} words</span>
                  </Link>
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
