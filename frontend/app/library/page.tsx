"use client"
import { useEffect, useState } from "react"
import Link from "next/link"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

interface Bookmark { id: string; title: string; author: string; site: string; url: string; savedAt: string }
interface ProgressEntry { chapter: number; at: string; title: string }
type Tab = "bookmarks" | "reading" | "searches" | "import"

export default function LibraryPage() {
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([])
  const [progress, setProgress] = useState<Record<string, ProgressEntry>>({})
  const [recents, setRecents] = useState<string[]>([])
  const [tab, setTab] = useState<Tab>("bookmarks")

  // Import state
  const [importUrl, setImportUrl] = useState("")
  const [importing, setImporting] = useState(false)
  const [importMsg, setImportMsg] = useState<string | null>(null)
  const [importError, setImportError] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)

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

  const importFromUrl = async () => {
    if (!importUrl.trim()) return
    setImporting(true); setImportError(null); setImportMsg(null)
    try {
      const fd = new FormData()
      fd.append("url", importUrl.trim())
      const r = await fetch(`${API_BASE}/api/library/import-url`, { method: "POST", body: fd })
      if (!r.ok) {
        const err = await r.text()
        throw new Error(err || `HTTP ${r.status}`)
      }
      const data = await r.json()
      setImportMsg(`Imported "${data.title}" (${data.chapters} chapters). It's now searchable and readable in-app.`)
      setImportUrl("")
    } catch (e: any) {
      setImportError(e.message || "Import failed")
    } finally {
      setImporting(false)
    }
  }

  const uploadEpub = async (file: File) => {
    setImporting(true); setImportError(null); setImportMsg(null)
    try {
      const fd = new FormData()
      fd.append("file", file)
      const r = await fetch(`${API_BASE}/api/library/upload-epub`, { method: "POST", body: fd })
      if (!r.ok) {
        const err = await r.text()
        throw new Error(err || `HTTP ${r.status}`)
      }
      const data = await r.json()
      setImportMsg(`Uploaded "${data.title}" (${data.chapters} chapters).`)
    } catch (e: any) {
      setImportError(e.message || "Upload failed")
    } finally {
      setImporting(false)
    }
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
          Reading <span className="library-tab__count">{Object.keys(progress).length}</span>
        </button>
        <button className={`library-tab ${tab === "searches" ? "library-tab--on" : ""}`} onClick={() => setTab("searches")}>
          Searches <span className="library-tab__count">{recents.length}</span>
        </button>
        <button className={`library-tab ${tab === "import" ? "library-tab--on" : ""}`} onClick={() => setTab("import")}>
          Import
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

      {tab === "import" && (
        <div className="import-pane">
          <section className="import-section">
            <h3>Import from URL</h3>
            <p className="import-help">
              Paste an AO3 or FanFiction.net link. We&apos;ll fetch the full text via FicHub
              and make it searchable and readable in-app.
            </p>
            <div className="import-row">
              <input
                type="url"
                className="import-input"
                placeholder="https://archiveofourown.org/works/12345"
                value={importUrl}
                onChange={e => setImportUrl(e.target.value)}
                disabled={importing}
                onKeyDown={e => e.key === "Enter" && importFromUrl()}
              />
              <button className="btn btn--primary" onClick={importFromUrl} disabled={importing || !importUrl}>
                {importing ? "Importing…" : "Import"}
              </button>
            </div>
          </section>

          <section className="import-section">
            <h3>Upload EPUB</h3>
            <p className="import-help">
              Drop an .epub file you already have. It&apos;ll be added to your library.
            </p>
            <label
              className={`import-drop ${dragOver ? "import-drop--over" : ""}`}
              onDragOver={e => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={e => {
                e.preventDefault()
                setDragOver(false)
                const f = e.dataTransfer.files?.[0]
                if (f && f.name.toLowerCase().endsWith(".epub")) uploadEpub(f)
                else setImportError("Drop a .epub file")
              }}
            >
              <input
                type="file"
                accept=".epub"
                onChange={e => e.target.files?.[0] && uploadEpub(e.target.files[0])}
                disabled={importing}
                style={{ display: "none" }}
              />
              <div className="import-drop__inner">
                <span className="import-drop__icon">{importing ? "⋯" : "↓"}</span>
                <span className="import-drop__text">
                  {importing
                    ? "Uploading…"
                    : dragOver
                      ? "Drop to upload"
                      : <>Drag an EPUB here<br /><span className="import-drop__sub">or click to choose</span></>}
                </span>
              </div>
            </label>
          </section>

          {importMsg   && <div className="alert alert--success">{importMsg}</div>}
          {importError && <div className="alert alert--error">{importError}</div>}
        </div>
      )}
    </div>
  )
}
