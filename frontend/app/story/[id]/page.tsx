"use client"
import { useEffect, useState } from "react"
import { useParams } from "next/navigation"
import Link from "next/link"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (typeof window !== "undefined" ? `${window.location.protocol}//${window.location.hostname}:8000` : "http://localhost:8000")

interface ChapterMeta { id: string; number: number; title?: string; word_count: number }
interface StoryDetail {
  id: string; site: string; url: string; title: string; author: string;
  author_url?: string; summary?: string; language: string; rating?: string;
  status: string; word_count: number; chapter_count: number;
  chapter_count_total?: number; kudos: number; hits: number; bookmarks: number;
  comments: number; fandoms: string[]; relationships: string[]; characters: string[];
  tags: string[]; warnings: string[]; categories: string[];
  published_at?: string; updated_at?: string;
  is_hosted: boolean; wayback_url?: string; chapters: ChapterMeta[]
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
    const list = JSON.parse(localStorage.getItem("ficatlas:bookmarks") ?? "[]")
    setBookmarked(list.some((b: any) => b.id === story.id))
  }, [story])

  const toggleBookmark = () => {
    if (!story) return
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
          {story.is_hosted && story.chapters.length > 0 && (
            <Link href={`/story/${story.id}/chapter/1`} className="btn btn--primary">
              Read Chapter 1
            </Link>
          )}
          {!story.is_hosted && (
            <a href={story.url} target="_blank" rel="noopener noreferrer" className="btn btn--primary">
              Read on {SITE_LABELS[story.site]} ↗
            </a>
          )}
          <button className={`btn ${bookmarked ? "btn--on" : ""}`} onClick={toggleBookmark}>
            {bookmarked ? "★ Bookmarked" : "☆ Bookmark"}
          </button>
          {story.wayback_url && (
            <a href={story.wayback_url} target="_blank" rel="noopener noreferrer" className="btn btn--ghost">
              Wayback ↗
            </a>
          )}
        </div>

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

        {story.relationships.length > 0 && (
          <section className="story-detail__taggroup">
            <h4>Relationships</h4>
            <div className="tag-list">{story.relationships.map(r => <span key={r} className="tag tag--ship">{r}</span>)}</div>
          </section>
        )}
        {story.characters.length > 0 && (
          <section className="story-detail__taggroup">
            <h4>Characters</h4>
            <div className="tag-list">{story.characters.map(c => <span key={c} className="tag">{c}</span>)}</div>
          </section>
        )}
        {story.tags.length > 0 && (
          <section className="story-detail__taggroup">
            <h4>Tags</h4>
            <div className="tag-list">{story.tags.map(t => <span key={t} className="tag">{t}</span>)}</div>
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
