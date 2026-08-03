"use client"
import { useEffect, useState, useRef } from "react"
import Link from "next/link"
import OfflineLink from "../OfflineLink"
import { listOfflineStories, deleteOfflineStory } from "@/lib/offline"
import SiteHeader from "../SiteHeader"
import { useAuth } from "@/lib/auth"
import { pollJob } from "@/lib/pollJob"

const API_BASE_C = ""

// Fandom input with index-first + AO3-canonical autocomplete. Suggesting from the
// index is instant; falling back to AO3's canonical tags lets the user discover and
// correctly spell NEW fandoms to scrape (and avoids the malformed-tag URLs that
// broke earlier discover jobs). A drop-in replacement for a plain text input.
function CanonicalFandomInput({
  value, onChange, placeholder, disabled, onEnter, kind = "fandom",
}: {
  value: string; onChange: (v: string) => void; placeholder?: string
  disabled?: boolean; onEnter?: () => void
  kind?: "fandom" | "relationship" | "character" | "tag"
}) {
  const [sugs, setSugs] = useState<{ value: string; count: number | null; source: string }[]>([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const boxRef = useRef<HTMLDivElement | null>(null)
  const seq = useRef(0)

  useEffect(() => {
    const q = value.trim()
    if (q.length < 2) { setSugs([]); return }
    const mySeq = ++seq.current
    const t = setTimeout(async () => {
      try {
        const r = await fetch(`${API_BASE_C}/api/stats/suggest-canonical?kind=${kind}&q=${encodeURIComponent(q)}&limit=8`)
        if (r.ok && mySeq === seq.current) {
          setSugs(await r.json()); setOpen(true); setActive(-1)
        }
      } catch {}
    }, 250)
    return () => clearTimeout(t)
  }, [value, kind])

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("click", close)
    return () => document.removeEventListener("click", close)
  }, [open])

  const pick = (v: string) => { onChange(v); setSugs([]); setOpen(false); setActive(-1) }

  return (
    <div className="tag-input" ref={boxRef} style={{ flex: 1 }}>
      <input type="text" className="import-input" style={{ width: "100%" }}
        placeholder={placeholder} value={value} disabled={disabled}
        onChange={e => onChange(e.target.value)}
        onFocus={() => { if (sugs.length) setOpen(true) }}
        onKeyDown={e => {
          if (open && sugs.length) {
            if (e.key === "ArrowDown") { e.preventDefault(); setActive(i => Math.min(i + 1, sugs.length - 1)); return }
            if (e.key === "ArrowUp")   { e.preventDefault(); setActive(i => Math.max(i - 1, -1)); return }
            if (e.key === "Enter" && active >= 0) { e.preventDefault(); pick(sugs[active].value); return }
            if (e.key === "Escape") { setOpen(false); return }
          }
          if (e.key === "Enter" && onEnter) onEnter()
        }} />
      {open && sugs.length > 0 && (
        <ul className="tag-suggest">
          {sugs.map((s, i) => (
            <li key={s.value + s.source}
              className={`tag-suggest__item ${i === active ? "tag-suggest__item--active" : ""}`}
              onMouseDown={e => { e.preventDefault(); pick(s.value) }}
              onMouseEnter={() => setActive(i)}>
              <span className="tag-suggest__value">{s.value}</span>
              <span className="tag-suggest__count">
                {s.source === "ao3" ? "AO3" : (s.count != null ? s.count.toLocaleString() : "")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

const API_BASE = ""  // relative — handled by Next.js rewrite to backend

interface Bookmark { id: string; title: string; author: string; site: string; url: string; savedAt: string }
interface ProgressEntry { chapter: number; at: string; title: string }
interface HostedStory { id: string; title: string; author: string; site: string; word_count: number; chapter_count: number; summary?: string; tags: string[] }
// "searches" is gone — recent searches now sit under the search bar, which is
// where you are when you want to re-run one.
type Tab = "hosted" | "bookmarks" | "reading" | "offline" | "import"

export default function LibraryPage() {
  const { user, loading: authLoading } = useAuth()
  // Aborts any in-flight job poll when the page unmounts, so a scrape left
  // running server-side stops driving setState on a component that is gone.
  const pollAbort = useRef<AbortController | null>(null)
  useEffect(() => {
    pollAbort.current = new AbortController()
    return () => pollAbort.current?.abort()
  }, [])
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([])
  const [progress, setProgress] = useState<Record<string, ProgressEntry>>({})
  const [hosted, setHosted] = useState<HostedStory[]>([])
  const [hostedTotal, setHostedTotal] = useState(0)
  const [loadingMore, setLoadingMore] = useState(false)
  const [tab, setTab] = useState<Tab>("hosted")
  const [offlineStories, setOfflineStories] = useState<any[]>([])
  useEffect(() => {
    if (tab === "offline") listOfflineStories().then(setOfflineStories).catch(() => {})
  }, [tab])
  const removeOfflineStory = async (id: string) => {
    await deleteOfflineStory(id).catch(() => {})
    setOfflineStories(s => s.filter(x => x.id !== id))
  }
  // Preface/mis-split chapter cleanup
  const [prefaceBusy, setPrefaceBusy] = useState(false)
  const [prefaceMsg, setPrefaceMsg] = useState<string | null>(null)
  const cleanupPreface = async (dryRun: boolean) => {
    setPrefaceBusy(true); setPrefaceMsg(dryRun ? "Scanning…" : "Fixing…")
    try {
      const fd = new FormData()
      fd.append("dry_run", String(dryRun))
      const r = await fetch(`${API_BASE}/api/library/cleanup-preface-chapters`, { method: "POST", body: fd })
      let data: any = null
      try { data = await r.json() } catch { /* server returned non-JSON (e.g. 500 page) */ }
      if (!r.ok || !data) { setPrefaceMsg(`Failed (server error ${r.status}). Check backend logs.`); return }
      const n = data.affected_count ?? 0
      setPrefaceMsg(dryRun
        ? (n === 0 ? "✓ No mis-split chapters found." : `Found ${n} stor${n === 1 ? "y" : "ies"} with a front-matter first chapter. Click "Fix" to clean them up.`)
        : (n === 0 ? "✓ Nothing needed fixing." : `✓ Fixed ${n} stor${n === 1 ? "y" : "ies"} — front-matter removed and chapters renumbered.`))
    } catch (e: any) {
      setPrefaceMsg(`Error: ${e.message || e}`)
    } finally {
      setPrefaceBusy(false)
    }
  }

  // Import state
  const [importUrl, setImportUrl] = useState("")
  const [importing, setImporting] = useState(false)
  const [importMsg, setImportMsg] = useState<string | null>(null)
  const [importError, setImportError] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<{ done: number; total: number } | null>(null)
  const [uploadErrors, setUploadErrors] = useState<{ filename: string; error: string }[]>([])

  // Bulk URL import
  const [bulkUrls, setBulkUrls] = useState("")
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number; ok: number; failed: number } | null>(null)
  const [bulkResults, setBulkResults] = useState<{ url: string; ok: boolean; detail: string }[]>([])

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

  // DLP library discovery
  const [dlpCorpus, setDlpCorpus] = useState<"hp" | "other">("hp")
  const [dlpBusy, setDlpBusy] = useState(false)
  const [dlpEntries, setDlpEntries] = useState<any[]>([])
  const [dlpMsg, setDlpMsg] = useState<string | null>(null)
  const [dlpAutoImport, setDlpAutoImport] = useState(false)

  // AO3 deep-filtered discovery (paginated tag-works)
  const [a3Fandom, setA3Fandom] = useState("")
  const [a3MinWords, setA3MinWords] = useState("")
  const [a3MaxWords, setA3MaxWords] = useState("")
  const [a3CompleteOnly, setA3CompleteOnly] = useState(false)
  const [a3Sort, setA3Sort] = useState("revised_at")
  const [a3Pages, setA3Pages] = useState("3")
  const [a3Busy, setA3Busy] = useState(false)
  const [a3Msg, setA3Msg] = useState<string | null>(null)

  // HPFFA discovery (via AO3 Open Doors collection)
  const [hpMinWords, setHpMinWords] = useState("")
  const [hpMaxWords, setHpMaxWords] = useState("")
  const [hpCompleteOnly, setHpCompleteOnly] = useState(false)
  const [hpSort, setHpSort] = useState("revised_at")
  const [hpPages, setHpPages] = useState("3")
  const [hpBusy, setHpBusy] = useState(false)
  const [hpMsg, setHpMsg] = useState<string | null>(null)
  // Extra HP archive sources + dedup
  const [archBusy, setArchBusy] = useState<string | null>(null)
  const [archMsg, setArchMsg] = useState<string | null>(null)

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
    // The endpoint now returns {total, items}; it used to return a bare list,
    // so the tab showed the page size (100) as though it were the whole shelf.
    fetch(`${API_BASE}/api/library/hosted?limit=100`).then(r => r.json()).then(d => {
      setHosted(Array.isArray(d) ? d : (d.items ?? []))
      setHostedTotal(Array.isArray(d) ? d.length : (d.total ?? 0))
    }).catch(() => {})
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

  const importBulkUrls = async () => {
    // Parse one URL per line, dedupe, keep only http(s) links.
    const urls = Array.from(new Set(
      bulkUrls.split(/[\n,\s]+/).map(u => u.trim()).filter(u => /^https?:\/\//i.test(u))
    ))
    if (urls.length === 0) { setImportError("Paste at least one valid http(s) URL, one per line."); return }
    setBulkBusy(true); setImportError(null); setImportMsg(null)
    setBulkResults([])
    setBulkProgress({ done: 0, total: urls.length, ok: 0, failed: 0 })
    const results: { url: string; ok: boolean; detail: string }[] = []
    let ok = 0, failed = 0
    // Sequential to be polite to FicHub and avoid hammering. ~1–3s each.
    for (let i = 0; i < urls.length; i++) {
      const url = urls[i]
      try {
        const fd = new FormData(); fd.append("url", url)
        const r = await fetch(`${API_BASE}/api/library/import-url`, { method: "POST", body: fd })
        if (!r.ok) {
          const t = await r.text().catch(() => "")
          throw new Error(t || `HTTP ${r.status}`)
        }
        const data = await r.json()
        ok++
        results.push({ url, ok: true, detail: `${data.title || "Imported"} (${data.chapters ?? "?"} ch)` })
      } catch (e: any) {
        failed++
        results.push({ url, ok: false, detail: (e.message || "failed").slice(0, 120) })
      }
      setBulkProgress({ done: i + 1, total: urls.length, ok, failed })
      setBulkResults([...results])
    }
    setBulkBusy(false)
    setImportMsg(`Bulk import done — ${ok} imported, ${failed} failed of ${urls.length}.`)
    loadHosted()
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

  // Elapsed-seconds counter ticks every second while a scrape is in flight,
  // so the button + status visibly change even though the underlying fetch
  // is one long-running request.
  const [a3Elapsed, setA3Elapsed] = useState(0)
  useEffect(() => {
    if (!a3Busy) { setA3Elapsed(0); return }
    const t = setInterval(() => setA3Elapsed(s => s + 1), 1000)
    return () => clearInterval(t)
  }, [a3Busy])

  const discoverAo3Deep = async () => {
    // Collapse internal whitespace runs to a single space — pasted tag names
    // routinely contain double spaces ("Harry Potter  - J. K. Rowling") that
    // AO3 doesn't recognise. Trim trailing/leading too.
    const fandom = a3Fandom.replace(/\s+/g, " ").trim()
    if (!fandom) {
      setA3Msg("⚠ Enter a fandom name first (e.g. 'Harry Potter - J. K. Rowling')")
      return
    }
    if (fandom !== a3Fandom) setA3Fandom(fandom)

    console.log("[ficatlas] Deep AO3 scrape starting", {
      fandom, minWords: a3MinWords, maxWords: a3MaxWords,
      completeOnly: a3CompleteOnly, sort: a3Sort, pages: a3Pages,
    })
    setA3Busy(true); setA3Msg("🔄 Starting AO3 scrape job…")
    const t0 = Date.now()

    try {
      const fd = new FormData()
      fd.append("fandom", fandom)
      if (a3MinWords.trim()) fd.append("min_words", a3MinWords.trim())
      if (a3MaxWords.trim()) fd.append("max_words", a3MaxWords.trim())
      if (a3CompleteOnly) fd.append("complete_only", "true")
      fd.append("sort", a3Sort)
      fd.append("max_pages", a3Pages || "3")
      const startR = await fetch(`${API_BASE}/api/library/discover-ao3`, { method: "POST", body: fd })
      const startData = await startR.json().catch(() => ({}))

      // 429 → AO3 is in cooldown. The backend has already refused; give the user
      // a clear message and don't start a doomed job.
      if (startR.status === 429) {
        setA3Msg(`⏳ ${startData.detail || "AO3 is blocking our IP — cooldown active."} ` +
          `Try again in a few minutes, or use the HF dump / DLP / FicHub paths which work fine.`)
        return
      }
      if (!startR.ok || !startData.job_id) {
        setA3Msg(`❌ Failed to start: HTTP ${startR.status} — ${startData.detail || "no job_id returned"}`)
        return
      }
      const jobId = startData.job_id
      console.log("[ficatlas] job started", jobId)

      // 2) Poll for progress every 1.5s
      try {
        const j = await pollJob(jobId, {
          signal: pollAbort.current?.signal,
          onProgress: st => setA3Msg(
            `🔄 ${st.progress} (pages: ${st.pages_ok || 0} ok / ${st.pages_failed || 0} failed, ${st.found || 0} works)`),
        })
        const dt = ((Date.now() - t0) / 1000).toFixed(1)
        if (j.status === "error") {
          const msg = j.error || "unknown error"
          // Special-case the throttle/block path so the user knows it's not a bug.
          if (msg.includes("timeout") || msg.includes("throttl") || msg.includes("525")) {
            setA3Msg(`⛔ AO3 didn't respond in time (after ${dt}s). Its filtered pages are slow to generate and occasionally return a Cloudflare error when their origin is overloaded — usually transient. Try again in a minute or two, or use the HF dump / DLP / FicHub paths instead.`)
          } else {
            setA3Msg(`❌ Job failed after ${dt}s: ${msg}`)
          }
        } else if (j.found === 0) {
          setA3Msg(`✓ Done in ${dt}s — 0 works matched (${j.pages_ok} pages OK${j.pages_failed ? `, ${j.pages_failed} failed` : ""}). Try a broader filter or the canonical AO3 tag.`)
        } else {
          setA3Msg(`✓ Done in ${dt}s — ${j.found} works, ${j.newly_indexed} new to the index.`)
        }
      } catch (e: any) {
        if (e?.kind !== "aborted") setA3Msg(`❌ ${e.message || e}`)
      }
      return
    } catch (e: any) {
      const dt = ((Date.now() - t0) / 1000).toFixed(1)
      console.error("[ficatlas] Deep AO3 scrape failed", e)
      setA3Msg(`❌ Network error after ${dt}s: ${e.message || e}.`)
    } finally {
      setA3Busy(false)
    }
  }

  const discoverHpffa = async () => {
    setHpBusy(true); setHpMsg("🔄 Starting HPFFA collection scrape job…")
    const t0 = Date.now()
    try {
      const fd = new FormData()
      if (hpMinWords.trim()) fd.append("min_words", hpMinWords.trim())
      if (hpMaxWords.trim()) fd.append("max_words", hpMaxWords.trim())
      if (hpCompleteOnly) fd.append("complete_only", "true")
      fd.append("sort", hpSort)
      fd.append("max_pages", hpPages || "3")
      const startR = await fetch(`${API_BASE}/api/library/discover-hpffa`, { method: "POST", body: fd })
      const startData = await startR.json().catch(() => ({}))
      if (!startR.ok || !startData.job_id) {
        setHpMsg(`❌ Failed to start: HTTP ${startR.status} — ${startData.detail || "no job_id returned"}`)
        return
      }
      const jobId = startData.job_id
      try {
        const jb = await pollJob(jobId, {
          signal: pollAbort.current?.signal,
          onProgress: st => setHpMsg(
            `🔄 ${st.progress} (pages: ${st.pages_ok || 0} ok / ${st.pages_failed || 0} failed, ${st.found || 0} works)`),
        })
        const dt = ((Date.now() - t0) / 1000).toFixed(1)
        if (jb.status === "error") setHpMsg(`❌ Job failed after ${dt}s: ${jb.error || "unknown error"}`)
        else setHpMsg(`✓ Done in ${dt}s — pulled ${jb.found} stories from HPFFA, added ${jb.newly_indexed} new to the index.`)
      } catch (e: any) {
        if (e?.kind !== "aborted") setHpMsg(`❌ ${e.message || e}`)
      }
    } catch (e: any) {
      setHpMsg(`❌ Network error: ${e.message || e}`)
    } finally {
      setHpBusy(false)
    }
  }

  // Generic async-job runner for the extra archive sources (HexFiles,
  // SquidgeWorld) and the cross-post dedup batch. All share the job-poll shape.
  const runArchiveJob = async (
    key: string, endpoint: string, label: string, body: Record<string, string> = {}
  ) => {
    setArchBusy(key); setArchMsg(`🔄 Starting ${label}…`)
    const t0 = Date.now()
    try {
      const fd = new FormData()
      for (const [k, v] of Object.entries(body)) fd.append(k, v)
      const startR = await fetch(`${API_BASE}/api/library/${endpoint}`, { method: "POST", body: fd })
      const startData = await startR.json().catch(() => ({}))
      if (!startR.ok || !startData.job_id) {
        setArchMsg(`❌ Failed to start ${label}: HTTP ${startR.status} — ${startData.detail || "no job_id"}`)
        return
      }
      const jobId = startData.job_id
      try {
        const jb = await pollJob(jobId, {
          signal: pollAbort.current?.signal,
          onProgress: st => setArchMsg(`🔄 ${st.progress}`),
        })
        const dt = ((Date.now() - t0) / 1000).toFixed(1)
        if (jb.status === "error") {
          setArchMsg(`❌ ${label} failed: ${jb.error || "unknown error"}`)
        } else {
          const extra = jb.newly_indexed != null
            ? ` — ${jb.found ?? 0} found, ${jb.newly_indexed} new`
            : (jb as any).duplicates_merged != null
              ? ` — merged ${(jb as any).duplicates_merged} duplicate rows across ${(jb as any).groups_found ?? 0} works`
              : ""
          setArchMsg(`✓ ${label} done in ${dt}s${extra}.`)
        }
      } catch (e: any) {
        if (e?.kind !== "aborted") setArchMsg(`❌ ${e.message || e}`)
      }
    } catch (e: any) {
      setArchMsg(`❌ Network error: ${e.message || e}`)
    } finally {
      setArchBusy(null)
    }
  }

  const discoverDlp = async () => {
    setDlpBusy(true); setDlpMsg(null); setDlpEntries([])
    try {
      const fd = new FormData()
      fd.append("corpus", dlpCorpus)
      fd.append("limit", "200")
      fd.append("auto_import", String(dlpAutoImport))
      const r = await fetch(`${API_BASE}/api/library/discover-dlp`, { method: "POST", body: fd })
      const data = await r.json()
      if (data.ok) {
        setDlpEntries(data.entries || [])
        const msg = dlpAutoImport
          ? `Found ${data.found} entries · imported ${data.imported}, skipped ${data.skipped}, failed ${data.failed}.`
          : `Found ${data.found} curated stories. Click Import on any to pull it.`
        setDlpMsg(msg)
        if (dlpAutoImport) loadHosted()
      } else {
        setDlpMsg(data.error || "DLP discovery failed")
      }
    } catch (e: any) {
      setDlpMsg(`Failed: ${e.message}`)
    } finally {
      setDlpBusy(false)
    }
  }

  const importDlpEntry = async (entry: any) => {
    const url = entry.urls?.ao3 || entry.urls?.ffn
    if (!url) { setImportError("This entry has no FFN/AO3 URL to import"); return }
    const fd = new FormData(); fd.append("url", url)
    try {
      const r = await fetch(`${API_BASE}/api/library/import-url`, { method: "POST", body: fd })
      const data = await r.json()
      setImportMsg(`Imported "${data.title}" (${data.chapters} chapters).`)
      setDlpEntries(entries => entries.filter(e => e !== entry))
      loadHosted()
    } catch (e: any) {
      setImportError(e.message || "Import failed")
    }
  }

  return (
    <div className="library-shell">
      <SiteHeader current="library" />
      <h1 className="library-title">My Library</h1>

      {/* Importing, uploading and the bulk scrapes modify the shared index, so
          they now require an account — they were completely unauthenticated
          before, including deleting hosted stories whose text is irreplaceable.
          Reading, bookmarks and offline copies stay available signed out. */}
      {!authLoading && !user && (
        <p className="library-signin-note">
          Browsing and offline reading work as normal.{" "}
          <Link href="/login" className="library-signin-note__link">Sign in</Link>{" "}
          to import stories, upload EPUBs, or run an archive scrape.
        </p>
      )}

      <div className="library-tabs">
        <button className={`library-tab ${tab === "hosted" ? "library-tab--on" : ""}`} onClick={() => setTab("hosted")}>
          Hosted <span className="library-tab__count">{hostedTotal || hosted.length}</span>
        </button>
        <button className={`library-tab ${tab === "bookmarks" ? "library-tab--on" : ""}`} onClick={() => setTab("bookmarks")}>
          Bookmarks <span className="library-tab__count">{bookmarks.length}</span>
        </button>
        <button className={`library-tab ${tab === "reading" ? "library-tab--on" : ""}`} onClick={() => setTab("reading")}>
          Reading <span className="library-tab__count">{Object.keys(progress).length}</span>
        </button>
        <button className={`library-tab ${tab === "offline" ? "library-tab--on" : ""}`} onClick={() => setTab("offline")}>
          Offline <span className="library-tab__count">{offlineStories.length}</span>
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
              <>
                <div className="books-grid">
                  {hosted.map(s => <BookCover key={s.id} story={s} onDelete={deleteHosted}
                    progress={progress[s.id]} />)}
                </div>
                {hosted.length < hostedTotal && (
                  <div className="library-more">
                    <button className="btn btn--ghost" disabled={loadingMore}
                      onClick={async () => {
                        setLoadingMore(true)
                        try {
                          const r = await fetch(
                            `${API_BASE}/api/library/hosted?limit=100&offset=${hosted.length}`)
                          const d = await r.json()
                          setHosted(h => [...h, ...(d.items ?? [])])
                          if (d.total) setHostedTotal(d.total)
                        } catch {} finally { setLoadingMore(false) }
                      }}>
                      {loadingMore ? "Loading…" : `Load more (${hosted.length} of ${hostedTotal.toLocaleString()})`}
                    </button>
                  </div>
                )}
              </>
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

      {tab === "offline" && (
        <div className="library-list">
          {offlineStories.length === 0
            ? <p className="library-empty">
                No stories saved offline yet. On any readable story&apos;s page, tap
                &quot;⤓ Save offline&quot; to download its chapters to this device — then you
                can read it with no connection (handy when you can&apos;t reach the server).
              </p>
            : <>
                <p className="offline-tab__note">
                  Saved on this device and readable with no connection. Stored in your browser —
                  clearing site data removes them.
                </p>
                {offlineStories.map(s => {
                  const p = progress[s.id]
                  const href = p?.chapter ? `/story/${s.id}/chapter/${p.chapter}` : `/story/${s.id}/chapter/1`
                  return (
                  <div key={s.id} className="library-item offline-item">
                    <OfflineLink href={href} className="offline-item__main">
                      <p className="library-item__title">{s.title}</p>
                      <p className="library-item__meta">
                        by {s.author} · {s.chapter_count} ch · {(s.word_count || 0).toLocaleString()} words ·
                        saved {new Date(s.savedAt).toLocaleDateString()}
                        {p?.chapter ? ` · resume ch ${p.chapter}` : ""}
                      </p>
                    </OfflineLink>
                    <button className="offline-item__remove" title="Remove from this device"
                      onClick={() => removeOfflineStory(s.id)}>✕</button>
                  </div>
                  )
                })}
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
            <h3>Bulk import from URL list</h3>
            <p className="import-help">
              Paste many AO3 / FanFiction.net links — one per line. Each is fetched
              via FicHub in turn (politely, ~1–3s each), so a long list takes a while.
              Leave the tab open while it runs.
            </p>
            <textarea
              className="import-input bulk-urls"
              placeholder={"https://archiveofourown.org/works/12345\nhttps://www.fanfiction.net/s/67890/1/\n…"}
              value={bulkUrls}
              onChange={e => setBulkUrls(e.target.value)}
              disabled={bulkBusy}
              rows={6}
            />
            <div className="import-row" style={{ marginTop: 8 }}>
              <button className="btn btn--primary" onClick={importBulkUrls} disabled={bulkBusy || !bulkUrls.trim()}>
                {bulkBusy && bulkProgress
                  ? `Importing ${bulkProgress.done}/${bulkProgress.total}…`
                  : "Import all"}
              </button>
              {bulkProgress && (
                <span className="bulk-stat">
                  ✓ {bulkProgress.ok} imported · ✗ {bulkProgress.failed} failed
                </span>
              )}
            </div>
            {bulkProgress && (
              <div className="bulk-progress-bar">
                <div className="bulk-progress-bar__fill"
                  style={{ width: `${(bulkProgress.done / bulkProgress.total) * 100}%` }} />
              </div>
            )}
            {bulkResults.length > 0 && (
              <ul className="bulk-results">
                {bulkResults.slice().reverse().map((r, i) => (
                  <li key={i} className={r.ok ? "bulk-result--ok" : "bulk-result--fail"}>
                    <span className="bulk-result__icon">{r.ok ? "✓" : "✗"}</span>
                    <span className="bulk-result__detail">{r.detail}</span>
                    <span className="bulk-result__url">{r.url.replace(/^https?:\/\//, "")}</span>
                  </li>
                ))}
              </ul>
            )}
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
              <input type="file"
                accept=".epub,application/epub+zip,application/zip"
                multiple
                onChange={e => e.target.files && uploadEpubs(e.target.files)}
                disabled={importing} style={{ display: "none" }} />
              <div className="import-drop__inner">
                <span className="import-drop__icon">{importing ? "⋯" : "↓"}</span>
                <span className="import-drop__text">
                  {importing ? "Uploading…" : dragOver ? "Drop to upload"
                    : <>Drag one or more EPUBs here<br />
                      <span className="import-drop__sub">or tap to choose · up to 100 at once · works on phone</span></>}
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
              <CanonicalFandomInput
                placeholder="Harry Potter - J. K. Rowling"
                value={feedFandom} onChange={setFeedFandom}
                disabled={feedBusy} onEnter={pollFeed} />
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
            <h3>Deep AO3 scrape (filtered, paginated)</h3>
            <p className="import-help">
              Where feeds give only the 25 newest works for a tag, this hits AO3&apos;s
              filtered works page directly — up to 20 works per page across multiple
              pages (5 pages ≈ 100 works matching your filters). AO3&apos;s filtered pages
              are slow to generate (~5–10s each), so a 5-page scrape typically takes
              30–60s; it runs as a background job with live progress, so you can keep
              browsing while it works.
            </p>
            <div className="import-row">
              <CanonicalFandomInput
                placeholder="Canonical tag, e.g. Harry Potter - J. K. Rowling"
                value={a3Fandom} onChange={setA3Fandom}
                disabled={a3Busy} onEnter={discoverAo3Deep} />
              <button className="btn btn--primary" onClick={discoverAo3Deep} disabled={a3Busy}>
                {a3Busy ? `Scraping… ${a3Elapsed}s` : "Scrape filtered"}
              </button>
            </div>
            {a3Busy && (
              <div className="alert" style={{marginTop:10, background:"var(--surface2)", borderColor:"var(--border)"}}>
                <span className="scrape-spinner" /> Working on it — {a3Elapsed}s elapsed.
                AO3&apos;s filtered pages take ~5–10s each to generate, so {a3Pages || "3"} pages
                usually takes {(parseInt(a3Pages || "3") * 7)}–{(parseInt(a3Pages || "3") * 10)}s. Watch the browser console (F12) for verbose logs.
              </div>
            )}
            <div className="feed-filters">
              <input type="number" className="setting-input" placeholder="Min words"
                value={a3MinWords} onChange={e => setA3MinWords(e.target.value)}
                style={{ flex: 1 }} min={0} step={1000} />
              <input type="number" className="setting-input" placeholder="Max words"
                value={a3MaxWords} onChange={e => setA3MaxWords(e.target.value)}
                style={{ flex: 1 }} min={0} step={1000} />
              <label className="feed-filters__check">
                <input type="checkbox" checked={a3CompleteOnly}
                  onChange={e => setA3CompleteOnly(e.target.checked)} />
                <span>Complete</span>
              </label>
            </div>
            <div className="feed-filters" style={{marginTop:6}}>
              <select className="setting-select" value={a3Sort}
                onChange={e => setA3Sort(e.target.value)} style={{flex:1}}>
                <option value="revised_at">Recently updated</option>
                <option value="created_at">Recently posted</option>
                <option value="kudos_count">Most kudos</option>
                <option value="word_count">Word count</option>
                <option value="hits">Most hits</option>
              </select>
              <input type="number" className="setting-input" placeholder="Pages"
                value={a3Pages} onChange={e => setA3Pages(e.target.value)}
                style={{ width: 80 }} min={1} max={20} />
            </div>
            {a3Msg && <div className="alert alert--success" style={{marginTop:10}}>{a3Msg}</div>}
          </section>

          <section className="import-section">
            <h3>HarryPotterFanfiction.com archive (via AO3 Open Doors)</h3>
            <p className="import-help">
              HPFFA closed to new works in 2016 and was imported wholesale to AO3 in late 2021 as
              the <code>hpfanfiction_hpff</code> collection (~85k stories). We pull from there so
              every result has full AO3 metadata — tags, characters, relationships, warnings — and
              imports work normally via FicHub. Stories get a <code>hpffa_archive</code> tag for
              easy filtering later.
            </p>
            <div className="import-row">
              <button className="btn btn--primary" onClick={discoverHpffa} disabled={hpBusy}>
                {hpBusy ? "Scraping HPFFA collection…" : "Scrape HPFFA collection"}
              </button>
            </div>
            <div className="feed-filters">
              <input type="number" className="setting-input" placeholder="Min words"
                value={hpMinWords} onChange={e => setHpMinWords(e.target.value)}
                style={{ flex: 1 }} min={0} step={1000} />
              <input type="number" className="setting-input" placeholder="Max words"
                value={hpMaxWords} onChange={e => setHpMaxWords(e.target.value)}
                style={{ flex: 1 }} min={0} step={1000} />
              <label className="feed-filters__check">
                <input type="checkbox" checked={hpCompleteOnly}
                  onChange={e => setHpCompleteOnly(e.target.checked)} />
                <span>Complete</span>
              </label>
            </div>
            <div className="feed-filters" style={{marginTop:6}}>
              <select className="setting-select" value={hpSort}
                onChange={e => setHpSort(e.target.value)} style={{flex:1}}>
                <option value="revised_at">Recently updated</option>
                <option value="created_at">Recently added to AO3</option>
                <option value="kudos_count">Most kudos</option>
                <option value="word_count">Word count</option>
                <option value="hits">Most hits</option>
              </select>
              <input type="number" className="setting-input" placeholder="Pages"
                value={hpPages} onChange={e => setHpPages(e.target.value)}
                style={{ width: 80 }} min={1} max={20} />
            </div>
            {hpMsg && <div className="alert alert--success" style={{marginTop:10}}>{hpMsg}</div>}
          </section>

          <section className="import-section">
            <h3>More Harry Potter archives</h3>
            <p className="import-help">
              Two further archives. <strong>The HexFiles</strong> (Harry Potter FanFic
              Archive) is a separate ~18k-member eFiction site that also moved to AO3
              Open Doors in 2021 (collection <code>harrypotterfanficarchive</code>);
              tagged <code>hexfiles_archive</code>. <strong>SquidgeWorld</strong> runs the
              same Otwarchive software as AO3 (~30k mostly-HP works) so we scrape its
              works listing directly; tagged <code>squidgeworld_archive</code>.
            </p>
            <div className="import-row" style={{ gap: 8, flexWrap: "wrap" }}>
              <button className="btn btn--primary"
                onClick={() => runArchiveJob("hexfiles", "discover-hexfiles", "HexFiles scrape", { max_pages: "5" })}
                disabled={archBusy !== null}>
                {archBusy === "hexfiles" ? "Scraping HexFiles…" : "Scrape HexFiles"}
              </button>
              <button className="btn btn--primary"
                onClick={() => runArchiveJob("squidge", "discover-squidgeworld", "SquidgeWorld scrape", { max_pages: "5" })}
                disabled={archBusy !== null}>
                {archBusy === "squidge" ? "Scraping SquidgeWorld…" : "Scrape SquidgeWorld"}
              </button>
            </div>
            <p className="import-help" style={{ marginTop: 14 }}>
              There's also a 112k-story metadata seed (titles/authors/summaries scraped
              with permission from AO3) you can bulk-import from the command line:
              <code>docker compose exec backend python janelleshane_importer.py --download</code>
            </p>
            {archMsg && <div className="alert alert--success" style={{ marginTop: 10 }}>{archMsg}</div>}
          </section>

          <section className="import-section">
            <h3>Fix mis-split chapters</h3>
            <p className="import-help">
              Some imports accidentally saved the work&apos;s title page or the author&apos;s
              preliminary notes as &quot;Chapter 1&quot;. This scans hosted stories, removes those
              front-matter pages, and renumbers the real chapters. Safe to run anytime —
              it only touches stories whose first chapter looks like a metadata/notes
              page, and never empties a story.
            </p>
            <div className="import-row" style={{ gap: 8, flexWrap: "wrap" }}>
              <button className="btn btn--ghost"
                onClick={() => cleanupPreface(true)}
                disabled={prefaceBusy}>
                {prefaceBusy ? "Scanning…" : "Preview affected"}
              </button>
              <button className="btn btn--primary"
                onClick={() => cleanupPreface(false)}
                disabled={prefaceBusy}>
                {prefaceBusy ? "Fixing…" : "Fix mis-split chapters"}
              </button>
            </div>
            {prefaceMsg && <div className="alert alert--success" style={{ marginTop: 10 }}>{prefaceMsg}</div>}
          </section>

          <section className="import-section">
            <h3>Merge cross-posted duplicates</h3>
            <p className="import-help">
              Many fics are posted on more than one site. This scans the index and merges
              copies of the same work (matched by title + author) into a single result
              with links to every version, keeping the most-recently-updated copy's hosted
              text. New imports are deduped automatically — run this once to clean up
              everything already indexed.
            </p>
            <div className="import-row">
              <button className="btn btn--primary"
                onClick={() => runArchiveJob("dedup", "dedup-crossposts", "Cross-post dedup")}
                disabled={archBusy !== null}>
                {archBusy === "dedup" ? "Merging duplicates…" : "Merge cross-posted duplicates"}
              </button>
            </div>
            {archBusy === "dedup" && archMsg && <div className="alert alert--success" style={{ marginTop: 10 }}>{archMsg}</div>}
          </section>

          <section className="import-section">
            <h3>Discover FF.net works (via Wayback)</h3>
            <p className="import-help">
              FF.net blocks direct server requests with a Cloudflare challenge (this is
              true for any server, regardless of IP), but the Wayback Machine&apos;s index
              isn&apos;t blocked. We pull FF.net story URLs Wayback has archived, then import
              each via FicHub. Filter by URL keyword (e.g. &quot;Harry-Potter&quot;) to narrow results.
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

          <section className="import-section">
            <h3>Discover via DarkLordPotter library</h3>
            <p className="import-help">
              DLP curates ~1000+ vetted Harry Potter fanfics with links to their FFN/AO3
              sources. We scrape the catalog and you can import any entry, or auto-import
              the whole list. DLP&apos;s curated tags get merged into each story&apos;s tags.
            </p>
            <div className="import-row">
              <select className="setting-select" value={dlpCorpus}
                onChange={e => setDlpCorpus(e.target.value as "hp" | "other")}
                disabled={dlpBusy}>
                <option value="hp">HP-only library</option>
                <option value="other">Other fandoms library</option>
              </select>
              <label className="feed-filters__check">
                <input type="checkbox" checked={dlpAutoImport}
                  onChange={e => setDlpAutoImport(e.target.checked)} disabled={dlpBusy} />
                <span>Auto-import all (slow, ~5min per 50 stories)</span>
              </label>
              <button className="btn btn--primary" onClick={discoverDlp} disabled={dlpBusy}>
                {dlpBusy ? (dlpAutoImport ? "Importing…" : "Fetching…") : "Fetch DLP library"}
              </button>
            </div>
            {dlpMsg && <div className="alert alert--success" style={{marginTop:10}}>{dlpMsg}</div>}
            {dlpEntries.length > 0 && !dlpAutoImport && (
              <ul className="dlp-entries">
                {dlpEntries.slice(0, 100).map((e, i) => (
                  <li key={i}>
                    <div className="dlp-entry__main">
                      <p className="dlp-entry__title">{e.title}</p>
                      <p className="dlp-entry__meta">
                        by {e.author}{e.rating ? ` · ${e.rating}` : ""}
                        {e.dlp_tags?.length ? ` · ${e.dlp_tags.filter((t: string) => !t.toLowerCase().startsWith("author")).slice(0, 4).join(", ")}` : ""}
                      </p>
                      <p className="dlp-entry__urls">
                        {Object.entries(e.urls || {}).map(([k, v]) => (
                          <a key={k} href={v as string} target="_blank" rel="noreferrer">{k}</a>
                        ))}
                      </p>
                    </div>
                    <button className="ffn-discover__import"
                      onClick={() => importDlpEntry(e)}
                      disabled={!e.urls?.ao3 && !e.urls?.ffn}>
                      {(e.urls?.ao3 || e.urls?.ffn) ? "Import" : "No URL"}
                    </button>
                  </li>
                ))}
                {dlpEntries.length > 100 && (
                  <li style={{textAlign:"center", color:"var(--text-faint)", fontSize:12, padding:8}}>
                    …and {dlpEntries.length - 100} more. Use Auto-import to grab them all.
                  </li>
                )}
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

          <section className="import-section import-section--docs">
            <h3>Bulk imports (server-side)</h3>
            <p className="import-help">
              For multi-gigabyte datasets — too big to upload through the browser — copy the
              file to the server then run the importer via <code>docker compose exec</code>. Each
              runs once and inserts everything matching your filter into the index.
            </p>

            <details className="bulk-importer">
              <summary><strong>FicAlley HTML dump</strong> — full text, ~30k Harry Potter stories</summary>
              <p>Imports stories from a local extract of the FicAlley archive (chapters and metadata).</p>
              <pre className="bulk-importer__cmd">{`sudo docker compose exec backend python fictionalley_importer.py --path /path/to/fictionalley`}</pre>
              <p className="bulk-importer__note">
                Source: archived FicAlley HTML directories. If you have a local copy, mount it into
                the backend container and pass <code>--path</code>.
              </p>
            </details>

            <details className="bulk-importer">
              <summary><strong>AO3 2021 metadata dump</strong> — ~5M works, titles/authors/tags only</summary>
              <p>Metadata-only import of the 2021 AO3 CSV release. Read these to find URLs; use URL import or DLP for full text.</p>
              <pre className="bulk-importer__cmd">{`sudo docker cp ao3_works.csv ficatlas-backend-1:/app/data/ao3_works.csv
sudo docker compose exec backend python ao3_dump_importer.py \\
  --file /app/data/ao3_works.csv --fandom "Harry Potter" --limit 50000`}</pre>
              <p className="bulk-importer__note">
                The 2021 dump was hosted at the AO3 admin posts page. If the original link 404s, the
                dump may have been deprecated — try Internet Archive or skip in favour of the deep
                AO3 scrape above, which gives live data.
              </p>
            </details>

            <details className="bulk-importer" open>
              <summary><strong>HuggingFace mrzjy/fanfiction_meta</strong> — 6.6M FFnet rows, metadata only ⭐ recommended</summary>
              <p>
                The best free seed source for FFnet, which (unlike AO3) is Cloudflare-blocked
                for direct server scraping regardless of your IP. Covers FFnet story IDs 1 to
                ~10.9M (roughly 2014-era). After import, click any story&apos;s &quot;Import &amp; Read&quot;
                button in search and FicHub will fetch the full text on-demand.
              </p>
              <pre className="bulk-importer__cmd">{`# One command — downloads ~2GB via huggingface_hub and imports HP subset.
# Skip --fandom and --limit to ingest all 6.6M rows (~30 min, all fandoms).
sudo docker compose exec backend python huggingface_meta_importer.py \\
  --download --fandom "Harry Potter" --limit 100000

# Or peek first with dry-run:
sudo docker compose exec backend python huggingface_meta_importer.py \\
  --download --dry-run --limit 50`}</pre>
              <p className="bulk-importer__note">
                Dataset: <code>huggingface.co/datasets/mrzjy/fanfiction_meta</code> (CC license).
                The script downloads parquet shards from the dataset&apos;s
                <code>refs/convert/parquet</code> branch and caches them in <code>/app/data/hf_cache</code>;
                re-running with the same flags re-uses the cache. <code>--fandom</code> is a
                substring match on the category column (e.g. matches both &quot;Harry Potter&quot; and
                &quot;Harry Potter, Twilight&quot; crossovers).
              </p>
            </details>

            <details className="bulk-importer">
              <summary><strong>FF.net 2015 SQLite</strong> — ~7M works, metadata only</summary>
              <p>Older academic dataset of FF.net works circa 2015. Superseded by the HuggingFace dump above (which is cleaner and current); use this only if you have the file already.</p>
              <pre className="bulk-importer__cmd">{`sudo docker cp fanfiction.db ficatlas-backend-1:/app/data/fanfiction.db
sudo docker compose exec backend python ffnet_sqlite_importer.py \\
  --path /app/data/fanfiction.db --fandom "Harry Potter" --limit 50000`}</pre>
              <p className="bulk-importer__note">
                Source: <code>archive.org/details/fanfic_dataset_2014_2015</code> (may be unavailable).
              </p>
            </details>

            <p className="bulk-importer__hint">
              Why CLI-only? These files are 600MB–3GB. HTTP upload would time out and chunked
              upload adds complexity for a one-time operation. <code>docker cp</code> + a server-side
              command is faster, more reliable, and matches how you&apos;d handle any large data load.
            </p>
          </section>

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

function BookCover({ story, onDelete, progress }: {
  story: HostedStory; onDelete: (id: string, title: string) => void;
  progress?: { chapter: number; totalChapters?: number; scrollPct?: number }
}) {
  const [g1, g2] = COVER_GRADIENTS[hashCode(story.title) % COVER_GRADIENTS.length]
  // Long titles get a smaller step; the actual size is resolved in CSS relative
  // to the cover's own width (container units), because a fixed pixel size
  // chosen here was sized for the desktop cover and overflowed the clamp on a
  // 2-column phone grid, where each cover is ~166px wide.
  const titleLen = story.title.length > 44 ? "xl" : story.title.length > 30 ? "l"
                 : story.title.length > 18 ? "m" : "s"

  // Compute % through story: (completed chapters + partial scroll) / total
  let pct: number | null = null
  let label = ""
  if (progress?.chapter && progress.chapter > 0) {
    const total = progress.totalChapters ?? story.chapter_count ?? 1
    const completed = Math.max(0, progress.chapter - 1)
    const partial = progress.scrollPct ?? 0
    pct = Math.min(1, (completed + partial) / Math.max(total, 1))
    label = `Ch ${progress.chapter}/${total} · ${Math.round(pct * 100)}%`
  }

  // Continue Reading: deep-link straight to the saved chapter
  const readHref = progress?.chapter
    ? `/story/${story.id}/chapter/${progress.chapter}`
    : `/story/${story.id}`

  return (
    <div className="book">
      <Link href={readHref} className="book__cover-link">
        <div className="book__cover" style={{ background: `linear-gradient(160deg, ${g1}, ${g2})` }}>
          <div className="book__cover-spine" />
          <div className="book__cover-content">
            <p className="book__cover-title" data-len={titleLen}>{story.title}</p>
            <p className="book__cover-author">{story.author}</p>
          </div>
          <div className="book__cover-shine" />
          {pct !== null && (
            <div className="book__progress" title={label}>
              <div className="book__progress-fill" style={{ width: `${pct * 100}%` }} />
            </div>
          )}
        </div>
      </Link>
      <div className="book__meta">
        <p className="book__title" title={story.title}>{story.title}</p>
        <p className="book__author">{story.author}</p>
        <p className="book__stats">
          {pct !== null ? label : `${story.chapter_count} ch · ${(story.word_count/1000).toFixed(0)}k words`}
        </p>
      </div>
      <button className="book__remove" title="Remove from library"
        onClick={(e) => { e.preventDefault(); onDelete(story.id, story.title) }}>✕</button>
    </div>
  )
}
