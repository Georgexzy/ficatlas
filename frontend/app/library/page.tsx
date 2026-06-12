"use client"
import { useEffect, useState } from "react"
import Link from "next/link"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

interface Bookmark { id: string; title: string; author: string; site: string; url: string; savedAt: string }
interface ProgressEntry { chapter: number; at: string; title: string }
interface HostedStory { id: string; title: string; author: string; site: string; word_count: number; chapter_count: number; summary?: string; tags: string[] }
type Tab = "hosted" | "bookmarks" | "reading" | "searches" | "import"

export default function LibraryPage() {
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([])
  const [progress, setProgress] = useState<Record<string, ProgressEntry>>({})
  const [recents, setRecents] = useState<string[]>([])
  const [hosted, setHosted] = useState<HostedStory[]>([])
  const [tab, setTab] = useState<Tab>("hosted")

  // Import state
  const [importUrl, setImportUrl] = useState("")
  const [importing, setImporting] = useState(false)
  const [importMsg, setImportMsg] = useState<string | null>(null)
  const [importError, setImportError] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<{ done: number; total: number } | null>(null)
  const [uploadErrors, setUploadErrors] = useState<{ filename: string; error: string }[]>([])

  // Feed discovery
  const [feedFandom, setFeedFandom] = useState("")
  const [feedBusy, setFeedBusy] = useState(false)
  const [feedMsg, setFeedMsg] = useState<string | null>(null)

  const loadHosted = () => {
    fetch(`${API_BASE}/api/library/hosted`).then(r => r.json()).then(setHosted).catch(() => {})
  }

  useEffect(() => {
    setBookmarks(JSON.parse(localStorage.getItem("ficatlas:bookmarks") ?? "[]"))
    setProgress(JSON.parse(localStorage.getItem("ficatlas:progress") ?? "{}"))
    setRecents(JSON.parse(localStorage.getItem("ficatlas:recent-searches") ?? "[]"))
    loadHosted()
  }, [])

  const removeBookmark = (id: string) => {
    const filtered = bookmarks.filter(b => b.id !== id)
    setBookmarks(filtered)
    localStorage.setItem("ficatlas:bookmarks", JSON.stringify(filtered))
  }
  const clearProgress = (id: string) => {
    const next = { ...progress }; delete next[id]
    setProgress(next)
    localStorage.setItem("ficatlas:progress", JSON.stringify(next))
  }
  const clearRecents = () => {
    setRecents([]); localStorage.setItem("ficatlas:recent-searches", "[]")
  }

  const importFromUrl = async () => {
    if (!importUrl.trim()) return
    setImporting(true); setImportError(null); setImportMsg(null)
    try {
      const fd = new FormData()
      fd.append("url", importUrl.trim())
      const r = await fetch(`${API_BASE}/api/library/import-url`, { method: "POST", body: fd })
      if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`)
      const data = await r.json()
      const ch = data.chapters ?? "?"
      setImportMsg(`Imported "${data.title}" (${ch} chapters). Find it in the Hosted tab.`)
      setImportUrl(""); loadHosted()
    } catch (e: any) {
      setImportError(e.message || "Import failed")
    } finally {
      setImporting(false)
    }
  }

  const uploadEpubs = async (fileList: FileList | File[]) => {
    const files = Array.from(fileList).filter(f => f.name.toLowerCase().endsWith(".epub"))
    if (files.length === 0) { setImportError("No .epub files found"); return }
    setImporting(true); setImportError(null); setImportMsg(null); setUploadErrors([])

    if (files.length === 1) {
      try {
        const fd = new FormData(); fd.append("file", files[0])
        const r = await fetch(`${API_BASE}/api/library/upload-epub`, { method: "POST", body: fd })
        if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`)
        const data = await r.json()
        setImportMsg(`Uploaded "${data.title}" (${data.chapters} chapters).`)
        loadHosted()
      } catch (e: any) { setImportError(e.message || "Upload failed") }
      finally { setImporting(false) }
      return
    }

    const CHUNK = 25
    let totalSucceeded = 0
    const allErrors: { filename: string; error: string }[] = []
    setUploadProgress({ done: 0, total: files.length })
    try {
      for (let i = 0; i < files.length; i += CHUNK) {
        const batch = files.slice(i, i + CHUNK)
        const fd = new FormData()
        batch.forEach(f => fd.append("files", f))
        const r = await fetch(`${API_BASE}/api/library/upload-epubs`, { method: "POST", body: fd })
        if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`)
        const data = await r.json()
        totalSucceeded += data.succeeded
        if (data.errors?.length) allErrors.push(...data.errors)
        setUploadProgress({ done: Math.min(i + CHUNK, files.length), total: files.length })
      }
      setImportMsg(`Imported ${totalSucceeded} of ${files.length} EPUBs.`)
      setUploadErrors(allErrors); loadHosted()
    } catch (e: any) { setImportError(e.message || "Bulk upload failed") }
    finally { setImporting(false); setTimeout(() => setUploadProgress(null), 3000) }
  }

  const uploadEpub = (file: File) => uploadEpubs([file])

  const pollFeed = async () => {
    if (!feedFandom.trim()) return
    setFeedBusy(true); setFeedMsg(null)
    try {
      const fd = new FormData(); fd.append("fandom", feedFandom.trim())
      const r = await fetch(`${API_BASE}/api/library/poll-feed`, { method: "POST", body: fd })
      const data = await r.json()
      if (data.ok) {
        setFeedMsg(`Found ${data.found} recent works for "${data.fandom}", added ${data.newly_indexed} new to the index. Search to see them.`)
      } else {
        setFeedMsg(data.error || "Feed poll failed")
      }
    } catch (e: any) {
      setFeedMsg(`Failed: ${e.message}`)
    } finally {
      setFeedBusy(false)
    }
  }

  return (
    <div className="library-shell">
      <Link href="/" className="back-link">← Back to search</Link>
      <h1 className="library-title">My Library</h1>

      <div className="library-tabs">
        <button className={`library-tab ${tab === "hosted" ? "library-tab--on" : ""}`} onClick={() => setTab("hosted")}>
          Hosted <span className="library-tab__count">{hosted.length}</span>
        </button>
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

      {tab === "hosted" && (
        <div className="library-list">
          {hosted.length === 0
            ? <p className="library-empty">No hosted stories yet. Import a URL or upload an EPUB in the Import tab — those become readable here.</p>
            : hosted.map(s => (
                <div key={s.id} className="library-item">
                  <Link href={`/story/${s.id}`} className="library-item__main">
                    <p className="library-item__title">{s.title}</p>
                    <p className="library-item__meta">
                      by {s.author} · {s.chapter_count} ch · {(s.word_count/1000).toFixed(0)}k words
                      {s.tags.includes("imported") && " · imported"}
                      {s.tags.includes("user upload") && " · uploaded"}
                    </p>
                  </Link>
                </div>
              ))}
        </div>
      )}

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
              <input type="url" className="import-input"
                placeholder="https://archiveofourown.org/works/12345"
                value={importUrl} onChange={e => setImportUrl(e.target.value)}
                disabled={importing} onKeyDown={e => e.key === "Enter" && importFromUrl()} />
              <button className="btn btn--primary" onClick={importFromUrl} disabled={importing || !importUrl}>
                {importing ? "Importing…" : "Import"}
              </button>
            </div>
          </section>

          <section className="import-section">
            <h3>Upload EPUB</h3>
            <p className="import-help">Drag one or more .epub files here, or click to choose. Up to 100 at once.</p>
            <label
              className={`import-drop ${dragOver ? "import-drop--over" : ""}`}
              onDragOver={e => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={e => { e.preventDefault(); setDragOver(false)
                if (e.dataTransfer.files?.length) uploadEpubs(e.dataTransfer.files)
                else setImportError("Drop one or more .epub files") }}>
              <input type="file" accept=".epub" multiple
                onChange={e => e.target.files && uploadEpubs(e.target.files)}
                disabled={importing} style={{ display: "none" }} />
              <div className="import-drop__inner">
                <span className="import-drop__icon">{importing ? "⋯" : "↓"}</span>
                <span className="import-drop__text">
                  {importing ? "Uploading…" : dragOver ? "Drop to upload"
                    : <>Drag one or more EPUBs here<br /><span className="import-drop__sub">or click to choose · up to 100 at once</span></>}
                </span>
              </div>
            </label>
          </section>

          <section className="import-section">
            <h3>Discover fresh AO3 works</h3>
            <p className="import-help">
              Pull the latest works for a fandom or ship straight from AO3&apos;s feed
              (e.g. &quot;Harry Potter - J. K. Rowling&quot; or &quot;Draco Malfoy/Hermione Granger&quot;).
              Only canonical AO3 tags have feeds. Newly found works get added to the search index.
            </p>
            <div className="import-row">
              <input type="text" className="import-input"
                placeholder="Harry Potter - J. K. Rowling"
                value={feedFandom} onChange={e => setFeedFandom(e.target.value)}
                disabled={feedBusy} onKeyDown={e => e.key === "Enter" && pollFeed()} />
              <button className="btn btn--primary" onClick={pollFeed} disabled={feedBusy || !feedFandom}>
                {feedBusy ? "Polling…" : "Pull latest"}
              </button>
            </div>
            {feedMsg && <div className="alert alert--success" style={{marginTop:10}}>{feedMsg}</div>}
          </section>

          {uploadProgress && (
            <div className="upload-progress">
              <div className="upload-progress__bar">
                <div className="upload-progress__fill" style={{ width: `${(uploadProgress.done / uploadProgress.total) * 100}%` }} />
              </div>
              <p className="upload-progress__text">{uploadProgress.done} / {uploadProgress.total} files</p>
            </div>
          )}

          {importMsg   && <div className="alert alert--success">{importMsg}</div>}
          {importError && <div className="alert alert--error">{importError}</div>}

          {uploadErrors.length > 0 && (
            <details className="upload-errors">
              <summary>{uploadErrors.length} file{uploadErrors.length === 1 ? "" : "s"} failed</summary>
              <ul>{uploadErrors.map((e, i) => <li key={i}><code>{e.filename}</code> — {e.error}</li>)}</ul>
            </details>
          )}
        </div>
      )}
    </div>
  )
}
