"use client"
import { useEffect, useState } from "react"
import Link from "next/link"

interface Bookmark { id: string; title: string; author: string; site: string; url: string; savedAt: string }
interface ProgressEntry { chapter: number; at: string; title: string }

export default function LibraryPage() {
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([])
  const [progress, setProgress] = useState<Record<string, ProgressEntry>>({})
  const [recents, setRecents] = useState<string[]>([])
  const [tab, setTab] = useState<"bookmarks" | "reading" | "searches">("bookmarks")

  useEffect(() => {
    setBookmarks(JSON.parse(localStorage.getItem("ficatlas:bookmarks") ?? "[]"))
    setProgress(JSON.parse(localStorage.getItem("ficatlas:progress") ?? "{}"))
    setRecents(JSON.parse(localStorage.getItem("ficatlas:recent-searches") ?? "[]"))
  }, [])

  const removeBookmark = (id: string) => {
    const filtered = bookmarks.filter(b => b.id !== id)
    setBookmarks(filtered)
    localStorage.setItem("ficatlas:bookmarks", JSON.stringify(filtered))
  }

  const clearProgress = (id: string) => {
    const next = { ...progress }
    delete next[id]
    setProgress(next)
    localStorage.setItem("ficatlas:progress", JSON.stringify(next))
  }

  const clearRecents = () => {
    setRecents([])
    localStorage.setItem("ficatlas:recent-searches", "[]")
  }

  return (
    <div className="library-shell">
      <Link href="/" className="back-link">← Back to search</Link>
      <h1 className="library-title">My Library</h1>

      <div className="library-tabs">
        <button className={`library-tab ${tab === "bookmarks" ? "library-tab--on" : ""}`} onClick={() => setTab("bookmarks")}>
          Bookmarks <span className="library-tab__count">{bookmarks.length}</span>
        </button>
        <button className={`library-tab ${tab === "reading" ? "library-tab--on" : ""}`} onClick={() => setTab("reading")}>
          Currently Reading <span className="library-tab__count">{Object.keys(progress).length}</span>
        </button>
        <button className={`library-tab ${tab === "searches" ? "library-tab--on" : ""}`} onClick={() => setTab("searches")}>
          Recent Searches <span className="library-tab__count">{recents.length}</span>
        </button>
      </div>

      {tab === "bookmarks" && (
        <div className="library-list">
          {bookmarks.length === 0
            ? <p className="library-empty">No bookmarks yet. Click ☆ on any story to save it.</p>
            : bookmarks.map(b => (
                <div key={b.id} className="library-item">
                  <Link href={`/story/${b.id}`} className="library-item__main">
                    <p className="library-item__title">{b.title}</p>
                    <p className="library-item__meta">by {b.author} · {b.site.toUpperCase()} · saved {new Date(b.savedAt).toLocaleDateString()}</p>
                  </Link>
                  <button className="library-item__remove" onClick={() => removeBookmark(b.id)}>✕</button>
                </div>
              ))}
        </div>
      )}

      {tab === "reading" && (
        <div className="library-list">
          {Object.keys(progress).length === 0
            ? <p className="library-empty">No reading in progress.</p>
            : Object.entries(progress).map(([id, p]) => (
                <div key={id} className="library-item">
                  <Link href={`/story/${id}/chapter/${p.chapter}`} className="library-item__main">
                    <p className="library-item__title">{p.title}</p>
                    <p className="library-item__meta">Chapter {p.chapter} · last read {new Date(p.at).toLocaleDateString()}</p>
                  </Link>
                  <button className="library-item__remove" onClick={() => clearProgress(id)}>✕</button>
                </div>
              ))}
        </div>
      )}

      {tab === "searches" && (
        <div className="library-list">
          {recents.length === 0
            ? <p className="library-empty">No recent searches.</p>
            : <>
                <button className="library-clear" onClick={clearRecents}>Clear all</button>
                {recents.map((q, i) => (
                  <Link key={i} href={`/?q=${encodeURIComponent(q)}`} className="library-item">
                    <p className="library-item__title">{q}</p>
                  </Link>
                ))}
              </>}
        </div>
      )}
    </div>
  )
}
