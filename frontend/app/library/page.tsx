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

  // Feed discovery (AO3)
  const [feedFandom, setFeedFandom] = useState("")
  const [feedMinWords, setFeedMinWords] = useState("")
  const [feedCompleteOnly, setFeedCompleteOnly] = useState(false)
  const [feedBusy, setFeedBusy] = useState(false)
  const [feedMsg, setFeedMsg] = useState<string | null>(null)

  // FFN discovery via Wayback
  const [ffnQuery, setFfnQuery] = useState("")
  const [ffnBusy, setFfnBusy] = useState(false)
  const [ffnUrls, setFfnUrls] = useState<{ url: string; story_id: string }[]>([])
  const [ffnMsg, setFfnMsg] = useState<string | null>(null)

  // Tracked fandom (auto-polled on every site load)
  const [trackedFandom, setTrackedFandom] = useState("")
  const [savingTracked, setSavingTracked] = useState(false)
  const [trackedSaved, setTrackedSaved] = useState(false)

  useEffect(() => {
    fetch(`${API_BASE}/api/settings`).then(r => r.json())
      .then(s => setTrackedFandom(s.tracked_fandom ?? ""))
      .catch(() => {})
  }, [])

  const saveTracked = async () => {
    setSavingTracked(true); setTrackedSaved(false)
    try {
      const fd = new FormData()
      fd.append("key", "tracked_fandom")
      fd.append("value", trackedFandom.trim())
      await fetch(`${API_BASE}/api/settings`, { method: "POST", body: fd })
      setTrackedSaved(true)
      // Immediately pull for the newly-set fandom
      const pf = new FormData(); pf.append("fandom", trackedFandom.trim())
      fetch(`${API_BASE}/api/library/poll-feed`, { method: "POST", body: pf }).catch(() => {})
      setTimeout(() => setTrackedSaved(false), 2500)
    } finally {
      setSavingTracked(false)
    }
  }

  const loadHosted = () => {
    fetch(`${API_BASE}/api/library/hosted`).then(r => r.json()).then(setHosted).catch(() => {})
  }

  const deleteHosted = async (id: string, title: string) => {
    if (!confirm(`Remove "${title}" from your library? This deletes the stored text.`)) return
    try {
      const r = await fetch(`${API_BASE}/api/library/hosted/${id}`, { method: "DELETE" })
      if (r.ok) {
        setHosted(h => h.filter(s => s.id !== id))
        try {
          const bm = JSON.parse(localStorage.getItem("ficatlas:bookmarks") ?? "[]")
          localStorage.setItem("ficatlas:bookmarks", JSON.stringify(bm.filter((b: any) => b.id !== id)))
        } catch { /* corrupt localStorage — ignore */ }
        try {
          const pg = JSON.parse(localStorage.getItem("ficatlas:progress") ?? "{}")
          delete pg[id]
          localStorage.setItem("ficatlas:progress", JSON.stringify(pg))
        } catch { /* same */ }
      }
    } catch {}
  }

  useEffect(() => {
    const safe = <T,>(key: string, fallback: T): T => {
      try { return JSON.parse(localStorage.getItem(key) ?? JSON.stringify(fallback)) } catch { return fallback }
    }
    setBookmarks(safe("ficatlas:bookmarks", []))
    setProgress(safe("ficatlas:progress", {}))
    setRecents(safe("ficatlas:recent-searches", []))
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
      const fd = new FormData()
      fd.append("fandom", feedFandom.trim())
      if (feedMinWords.trim()) fd.append("min_words", feedMinWords.trim())
      if (feedCompleteOnly) fd.append("complete_only", "true")
      const r = await fetch(`${API_BASE}/api/library/poll-feed`, { method: "POST", body: fd })
      const data = await r.json()
      if (data.ok) {
        const filtered = data.after_filter ?? data.found
        setFeedMsg(
          `Found ${data.found} recent works for "${data.fandom}"`
          + (filtered !== data.found ? `, ${filtered} matched filters` : "")
          + `, added ${data.newly_indexed} new to the index.`
        )
      } else {
        setFeedMsg(data.error || "Feed poll failed")
      }
    } catch (e: any) {
      setFeedMsg(`Failed: ${e.message}`)
    } finally {
      setFeedBusy(false)
    }
  }

  const discoverFfn = async () => {
    setFfnBusy(true); setFfnMsg(null); setFfnUrls([])
    try {
      const fd = new FormData()
      if (ffnQuery.trim()) fd.append("query", ffnQuery.trim())
      fd.append("limit", "30")
      const r = await fetch(`${API_BASE}/api/library/discover-ffnet`, { method: "POST", body: fd })
      const data = await r.json()
      if (data.ok) {
        setFfnUrls(data.urls || [])
        setFfnMsg(`Found ${data.found} FF.net URLs in the Wayback index. Click any to import via FicHub.`)
      } else {
        setFfnMsg(data.error || "Discovery failed")
      }
    } catch (e: any) {
      setFfnMsg(`Failed: ${e.message}`)
    } finally {
      setFfnBusy(false)
    }
  }

  const importDiscoveredUrl = async (url: string) => {
    const fd = new FormData(); fd.append("url", url)
    try {
      const r = await fetch(`${API_BASE}/api/library/import-url`, { method: "POST", body: fd })
      const data = await r.json()
      setImportMsg(`Imported "${data.title}" (${data.chapters} chapters).`)
      setFfnUrls(urls => urls.filter(u => u.url !== url))
      loadHosted()
    } catch (e: any) {
      setImportError(e.message || "Import failed")
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
        <div className="books-shelf">
          {hosted.length === 0
            ? <p className="library-empty">No hosted stories yet. Import a URL or upload an EPUB in the Import tab — those become readable here.</p>
            : (
              <div className="books-grid">
                {hosted.map(s => <BookCover key={s.id} story={s} onDelete={deleteHosted} />)}
              </div>
            )}
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
            <h3>Auto-pull on load</h3>
            <p className="import-help">
              Set a fandom or ship to pull automatically every time FicAtlas loads.
              The latest works from its AO3 feed get indexed in the background (debounced to once every 10 min).
            </p>
            <div className="import-row">
              <input type="text" className="import-input"
                placeholder="Harry Potter - J. K. Rowling"
                value={trackedFandom} onChange={e => setTrackedFandom(e.target.value)}
                disabled={savingTracked} onKeyDown={e => e.key === "Enter" && saveTracked()} />
              <button className="btn btn--primary" onClick={saveTracked} disabled={savingTracked || !trackedFandom}>
                {savingTracked ? "Saving…" : trackedSaved ? "✓ Saved" : "Save"}
              </button>
            </div>
          </section>

          <section className="import-section">
            <h3>Discover fresh AO3 works</h3><p className="import-help">
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
            <div className="feed-filters">
              <input type="number" className="setting-input" placeholder="Min words (e.g. 100000)"
                value={feedMinWords} onChange={e => setFeedMinWords(e.target.value)}
                style={{ flex: 1 }} min={0} step={1000} />
              <label className="feed-filters__check">
                <input type="checkbox" checked={feedCompleteOnly}
                  onChange={e => setFeedCompleteOnly(e.target.checked)} />
                <span>Complete only</span>
              </label>
            </div>
            {feedMsg && <div className="alert alert--success" style={{marginTop:10}}>{feedMsg}</div>}
          </section>

          <section className="import-section">
            <h3>Discover FF.net works (via Wayback)</h3>
            <p className="import-help">
              FF.net itself is blocked from our server by Cloudflare, but the Wayback
              Machine's index isn't. We pull FF.net story URLs Wayback has archived,
              then import each via FicHub. Filter by URL keyword (e.g. &quot;Harry-Potter&quot;)
              to narrow results.
            </p>
            <div className="import-row">
              <input type="text" className="import-input"
                placeholder="Harry-Potter (optional URL filter)"
                value={ffnQuery} onChange={e => setFfnQuery(e.target.value)}
                disabled={ffnBusy} onKeyDown={e => e.key === "Enter" && discoverFfn()} />
              <button className="btn btn--primary" onClick={discoverFfn} disabled={ffnBusy}>
                {ffnBusy ? "Searching…" : "Discover"}
              </button>
            </div>
            {ffnMsg && <div className="alert alert--success" style={{marginTop:10}}>{ffnMsg}</div>}
            {ffnUrls.length > 0 && (
              <ul className="ffn-discover-list">
                {ffnUrls.map(u => (
                  <li key={u.url}>
                    <code>{u.url}</code>
                    <button className="ffn-discover__import" onClick={() => importDiscoveredUrl(u.url)}>Import</button>
                  </li>
                ))}
              </ul>
            )}
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

// ── BookCover component (iOS Books-style cover with auto gradient by title hash) ──
function hashCode(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) { h = ((h << 5) - h) + s.charCodeAt(i); h |= 0 }
  return Math.abs(h)
}

// Curated palette: 12 muted, book-cover-appropriate gradient pairs
const COVER_GRADIENTS = [
  ["#3a2e2a", "#7a4f3a"],   // burnt sienna
  ["#1f2937", "#374151"],   // graphite
  ["#2d3748", "#4a5568"],   // slate
  ["#3c2a4d", "#6b4d8a"],   // plum
  ["#1e3a3a", "#3a6b6b"],   // teal forest
  ["#3a2a1e", "#7a5a3a"],   // tobacco
  ["#2a3a2d", "#4a7a55"],   // forest
  ["#4a2d2a", "#8a4a3a"],   // brick
  ["#2a2a3a", "#4a4a7a"],   // indigo dusk
  ["#3d2a3a", "#7a4a6a"],   // mauve
  ["#3a3d2a", "#7a7a3a"],   // olive gold
  ["#1f2d3a", "#3a5a7a"],   // navy mist
]

function BookCover({ story, onDelete }: { story: HostedStory; onDelete: (id: string, title: string) => void }) {
  const [g1, g2] = COVER_GRADIENTS[hashCode(story.title) % COVER_GRADIENTS.length]
  const fontSize = story.title.length > 30 ? 13 : story.title.length > 18 ? 15 : 17
  return (
    <div className="book">
      <Link href={`/story/${story.id}`} className="book__cover-link">
        <div className="book__cover" style={{ background: `linear-gradient(160deg, ${g1}, ${g2})` }}>
          <div className="book__cover-spine" />
          <div className="book__cover-content">
            <p className="book__cover-title" style={{ fontSize }}>{story.title}</p>
            <p className="book__cover-author">{story.author}</p>
          </div>
          <div className="book__cover-shine" />
        </div>
      </Link>
      <div className="book__meta">
        <p className="book__title" title={story.title}>{story.title}</p>
        <p className="book__author">{story.author}</p>
        <p className="book__stats">{story.chapter_count} ch · {(story.word_count/1000).toFixed(0)}k words</p>
      </div>
      <button className="book__remove" title="Remove from library"
        onClick={(e) => { e.preventDefault(); onDelete(story.id, story.title) }}>✕</button>
    </div>
  )
}
