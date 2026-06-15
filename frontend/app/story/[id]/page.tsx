"use client"
import { useEffect, useState } from "react"
import { useParams } from "next/navigation"
import Link from "next/link"

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

const SITE_LABELS: Record<string, string> = {
  ao3: "AO3", ffnet: "FF.net", fictionalley: "FicAlley",
  royalroad: "Royal Road", spacebattles: "SpaceBattles",
}

export default function StoryPage() {
  const params = useParams()
  const id = params?.id as string
  const [story, setStory] = useState<StoryDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [bookmarked, setBookmarked] = useState(false)
  const [importing, setImporting] = useState(false)

  useEffect(() => {
    if (!id) return
    fetch(`${API_BASE}/api/stories/${id}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(setStory)
      .catch(e => setError(String(e)))
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

  if (error) return <div className="reader-shell"><Link href="/" className="back-link">← Back to search</Link><div className="alert alert--error">{error}</div></div>
  if (!story) return <div className="reader-shell"><Link href="/" className="back-link">← Back to search</Link><p className="loading">Loading…</p></div>

  return (
    <div className="reader-shell">
      <Link href="/" className="back-link">← Back to search</Link>

      <article className="story-detail">
        <header className="story-detail__header">
          <p className="story-detail__site">{SITE_LABELS[story.site] ?? story.site}</p>
          <h1 className="story-detail__title">{story.title}</h1>
          <p className="story-detail__byline">
            by {story.author_url
              ? <a href={story.author_url} target="_blank" rel="noopener noreferrer">{story.author}</a>
              : story.author}
          </p>
          {story.fandoms.length > 0 && (
            <p className="story-detail__fandom">{story.fandoms.join(" · ")}</p>
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
          {!story.is_hosted && (story.site === "ao3" || story.site === "ffnet") && (
            <button className="btn btn--primary" onClick={importAndRead} disabled={importing}>
              {importing ? "Importing…" : "Import & Read here"}
            </button>
          )}
          {!story.is_hosted && story.site !== "ao3" && story.site !== "ffnet" && (
            <a href={story.site === "fictionalley"
                  ? `https://web.archive.org/web/2020/${story.url}`
                  : story.url}
              target="_blank" rel="noopener noreferrer" className="btn btn--primary">
              Read on {story.site === "fictionalley" ? "Wayback" : (SITE_LABELS[story.site] ?? story.site)} ↗
            </a>
          )}
          <button className={`btn ${bookmarked ? "btn--on" : ""}`} onClick={toggleBookmark}>
            {bookmarked ? "★ Bookmarked" : "☆ Bookmark"}
          </button>
          {/* For hosted FicAlley stories, expose Wayback alongside the in-app reader since FicAlley is defunct */}
          {story.is_hosted && story.site === "fictionalley" && (
            <a href={`https://web.archive.org/web/2020/${story.url}`}
              target="_blank" rel="noopener noreferrer" className="btn btn--ghost">
              View on Wayback ↗
            </a>
          )}
          {story.wayback_url && (
            <a href={story.wayback_url} target="_blank" rel="noopener noreferrer" className="btn btn--ghost">
              Wayback ↗
            </a>
          )}
          {story.cross_post_urls?.map(url => {
            const kind = url.includes("archiveofourown.org") ? "AO3"
                       : url.includes("fanfiction.net") ? "FF.net"
                       : "Mirror"
            return (
              <a key={url} href={url} target="_blank" rel="noopener noreferrer" className="btn btn--ghost">
                Also on {kind} ↗
              </a>
            )
          })}
        </div>

        {story.tags?.includes("dlp_library") && (
          <div className="dlp-banner">
            <span className="dlp-banner__icon">⚜</span>
            <span><strong>Curated by DarkLordPotter.</strong> This story is on DLP&apos;s recommended library list, with their curated tags applied.</span>
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
          <div><dt>Status</dt><dd>{story.status === "complete" ? "Complete" : "In Progress"}</dd></div>
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
      </article>
    </div>
  )
}
