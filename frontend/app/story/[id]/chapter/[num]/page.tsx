"use client"
import { useEffect, useState } from "react"
import { useParams, useRouter } from "next/navigation"
import Link from "next/link"

const API_BASE = ""  // relative — handled by Next.js rewrite to backend

interface ChapterFull {
  id: string; number: number; title?: string; summary?: string;
  content: string; word_count: number; posted_at?: string;
  start_note?: string; end_note?: string
}

interface StoryMin {
  id: string; title: string; author: string; chapter_count: number;
  chapters: { number: number; title?: string }[]
}

export default function ChapterPage() {
  const params = useParams()
  const router = useRouter()
  const storyId = params?.id as string
  const num = Number(params?.num)
  const [chapter, setChapter] = useState<ChapterFull | null>(null)
  const [story, setStory] = useState<StoryMin | null>(null)
  const [fontSize, setFontSize] = useState(17)
  const [fontFamily, setFontFamily] = useState<"serif" | "sans">("serif")
  const [width, setWidth] = useState<"narrow" | "wide">("narrow")
  const [lineHeight, setLineHeight] = useState(1.7)
  const [theme, setTheme] = useState<"default" | "sepia" | "dark">("default")
  const [scrollPct, setScrollPct] = useState(0)

  useEffect(() => {
    const savedSize = localStorage.getItem("ficatlas:reader-fontsize")
    if (savedSize) setFontSize(Number(savedSize))
    const savedFont = localStorage.getItem("ficatlas:reader_font")
    if (savedFont === "sans" || savedFont === "serif") setFontFamily(savedFont)
    const savedWidth = localStorage.getItem("ficatlas:reader_width")
    if (savedWidth === "narrow" || savedWidth === "wide") setWidth(savedWidth)
    const savedLH = localStorage.getItem("ficatlas:reader_lineheight")
    if (savedLH) setLineHeight(Number(savedLH))
    const savedTheme = localStorage.getItem("ficatlas:reader_theme")
    if (savedTheme === "sepia" || savedTheme === "dark" || savedTheme === "default") setTheme(savedTheme)

    // Fall back to server settings if localStorage is empty (different browser etc.)
    if (!savedFont || !savedWidth) {
      fetch(`${API_BASE}/api/settings`).then(r => r.json()).then(s => {
        if (!savedFont && (s.reader_font === "sans" || s.reader_font === "serif")) {
          setFontFamily(s.reader_font)
        }
        if (!savedWidth && (s.reader_width === "narrow" || s.reader_width === "wide")) {
          setWidth(s.reader_width)
        }
      }).catch(() => {})
    }
  }, [])

  useEffect(() => {
    localStorage.setItem("ficatlas:reader-fontsize", String(fontSize))
  }, [fontSize])
  useEffect(() => {
    localStorage.setItem("ficatlas:reader_font", fontFamily)
  }, [fontFamily])
  useEffect(() => {
    localStorage.setItem("ficatlas:reader_width", width)
  }, [width])
  useEffect(() => {
    localStorage.setItem("ficatlas:reader_lineheight", String(lineHeight))
  }, [lineHeight])
  useEffect(() => {
    localStorage.setItem("ficatlas:reader_theme", theme)
  }, [theme])

  // Reading progress bar
  useEffect(() => {
    const onScroll = () => {
      const h = document.documentElement
      const scrolled = h.scrollTop / (h.scrollHeight - h.clientHeight)
      setScrollPct(Math.min(100, Math.max(0, scrolled * 100)))
    }
    window.addEventListener("scroll", onScroll, { passive: true })
    return () => window.removeEventListener("scroll", onScroll)
  }, [])

  useEffect(() => {
    if (!storyId || !num) return
    let cancelled = false

    const fromOffline = async () => {
      // Fall back to the offline copy saved in IndexedDB (works with no network).
      const { getOfflineStory } = await import("@/lib/offline")
      const saved = await getOfflineStory(storyId)
      if (!saved || cancelled) return false
      const ch = saved.chapters.find(c => c.number === Number(num))
      if (!ch) return false
      setStory({
        id: saved.id, title: saved.title, author: saved.author,
        chapter_count: saved.chapter_count,
        chapters: saved.chapters.map(c => ({ number: c.number, title: c.title })),
      } as any)
      setChapter({
        number: ch.number, title: ch.title, content: ch.content,
        start_note: ch.start_note, end_note: ch.end_note, summary: ch.summary,
      } as any)
      return true
    }

    Promise.all([
      fetch(`${API_BASE}/api/stories/${storyId}`).then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json()
      }),
      fetch(`${API_BASE}/api/stories/${storyId}/chapters/${num}`).then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json()
      }),
    ])
      .then(([s, c]) => { if (!cancelled) { setStory(s); setChapter(c) } })
      .catch(async () => {
        // Network failed (offline or backend unreachable) — try the saved copy.
        const ok = await fromOffline()
        if (!ok && !cancelled) {
          setStory({ id: storyId, title: "Unavailable offline", author: "",
            chapter_count: 1, chapters: [] } as any)
          setChapter({ number: Number(num), title: "Unavailable offline",
            content: "<p>You're offline and this story isn't saved on this device. " +
                     "Open it while online, then tap “Save offline” on the story page to read it later.</p>" } as any)
        }
      })

    return () => { cancelled = true }
  }, [storyId, num])

  // Save last-read progress (chapter number + scroll position + total chapters)
  useEffect(() => {
    if (!story || !chapter) return
    try {
      const progress = JSON.parse(localStorage.getItem("ficatlas:progress") ?? "{}")
      progress[story.id] = {
        ...(progress[story.id] || {}),
        chapter: chapter.number,
        totalChapters: story.chapter_count,
        title: story.title,
        author: story.author,
        at: new Date().toISOString(),
      }
      localStorage.setItem("ficatlas:progress", JSON.stringify(progress))
    } catch {}
  }, [story, chapter])

  // Persist scroll position within chapter (debounced)
  useEffect(() => {
    if (!story || !chapter) return
    let timer: ReturnType<typeof setTimeout> | null = null
    const save = () => {
      try {
        const h = document.documentElement
        const denom = h.scrollHeight - h.clientHeight
        const pct = denom > 0 ? h.scrollTop / denom : 0
        const progress = JSON.parse(localStorage.getItem("ficatlas:progress") ?? "{}")
        progress[story.id] = {
          ...(progress[story.id] || {}),
          chapter: chapter.number,
          totalChapters: story.chapter_count,
          title: story.title,
          author: story.author,
          scrollPct: Math.min(1, Math.max(0, pct)),
          at: new Date().toISOString(),
        }
        localStorage.setItem("ficatlas:progress", JSON.stringify(progress))
      } catch {}
    }
    const onScroll = () => { if (timer) clearTimeout(timer); timer = setTimeout(save, 600) }
    window.addEventListener("scroll", onScroll, { passive: true })
    return () => { window.removeEventListener("scroll", onScroll); if (timer) clearTimeout(timer) }
  }, [story, chapter])

  // Restore scroll position once chapter content has rendered
  useEffect(() => {
    if (!story || !chapter) return
    try {
      const progress = JSON.parse(localStorage.getItem("ficatlas:progress") ?? "{}")
      const saved = progress[story.id]
      if (saved && saved.chapter === chapter.number && saved.scrollPct) {
        // Wait one paint so the content has laid out
        requestAnimationFrame(() => requestAnimationFrame(() => {
          const h = document.documentElement
          const target = (h.scrollHeight - h.clientHeight) * saved.scrollPct
          window.scrollTo({ top: target, behavior: "auto" })
        }))
      }
    } catch {}
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chapter?.number, story?.id])

  // Navigate between chapters. Offline, Next's client router would try a network
  // RSC fetch and fail, so use a hard navigation the service worker can serve.
  const goChapter = (n: number) => {
    const href = `/story/${storyId}/chapter/${n}`
    if (typeof navigator !== "undefined" && !navigator.onLine) window.location.href = href
    else router.push(href)
  }

  // Keyboard navigation
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return
      if (e.key === "ArrowLeft" && num > 1) goChapter(num - 1)
      if (e.key === "ArrowRight" && story && num < story.chapter_count) goChapter(num + 1)
      if (e.key === "+" || e.key === "=") setFontSize(s => Math.min(s + 1, 24))
      if (e.key === "-") setFontSize(s => Math.max(s - 1, 13))
      if (e.key === "t") setTheme(t => t === "default" ? "sepia" : t === "sepia" ? "dark" : "default")
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [num, story, storyId, router])

  if (!chapter || !story) return <div className="reader-shell"><p className="loading">Loading…</p></div>

  const hasPrev = num > 1
  const hasNext = num < story.chapter_count

  return (
    <div className="reader-shell" data-width={width} data-font={fontFamily} data-theme={theme}>
      <div className="reader-progress" style={{ width: `${scrollPct}%` }} />
      <div className="reader-topbar reader-topbar--sticky">
        <Link href={`/story/${storyId}`} className="back-link">← {story.title}</Link>
        <div className="reader-controls">
          <button className="reader-ctrl" onClick={() => setTheme(t => t === "default" ? "sepia" : t === "sepia" ? "dark" : "default")}
            title="Cycle theme (t)">{theme === "default" ? "◐ Light" : theme === "sepia" ? "◖ Sepia" : "◑ Dark"}</button>
          <button className="reader-ctrl" onClick={() => setFontFamily(f => f === "serif" ? "sans" : "serif")}
            title="Toggle serif / sans">{fontFamily === "serif" ? "Serif" : "Sans"}</button>
          <button className="reader-ctrl" onClick={() => setWidth(w => w === "narrow" ? "wide" : "narrow")}
            title="Toggle column width">{width === "narrow" ? "↔ Wide" : "↔ Narrow"}</button>
          <button className="reader-ctrl" onClick={() => setLineHeight(l => Math.max(1.3, +(l - 0.1).toFixed(1)))}
            title="Tighter line spacing" aria-label="Tighter lines">↕−</button>
          <button className="reader-ctrl" onClick={() => setLineHeight(l => Math.min(2.4, +(l + 0.1).toFixed(1)))}
            title="Looser line spacing" aria-label="Looser lines">↕+</button>
          <button className="reader-ctrl" onClick={() => setFontSize(s => Math.max(s - 1, 13))} aria-label="Smaller">A-</button>
          <button className="reader-ctrl" onClick={() => setFontSize(s => Math.min(s + 1, 24))} aria-label="Larger">A+</button>
        </div>
      </div>

      {/* Floating exit button — exit reader from anywhere without scrolling up */}
      <Link href={`/story/${storyId}`} className="reader-fab" title="Back to story page" aria-label="Exit reader">
        ✕
      </Link>

      <article className="reader" data-width={width} data-font={fontFamily} style={{ fontSize: `${fontSize}px`, lineHeight }}>
        <header className="reader__header">
          <p className="reader__breadcrumb">Chapter {num} of {story.chapter_count}</p>
          <h1 className="reader__title">{chapter.title || `Chapter ${num}`}</h1>
          {chapter.summary && <p className="reader__summary">{chapter.summary}</p>}
        </header>

        {chapter.start_note && (
          <aside className="reader__note">
            <p className="reader__note-label">Author's Note</p>
            <div dangerouslySetInnerHTML={{ __html: chapter.start_note }} />
          </aside>
        )}

        <div className="reader__body reader__content" dangerouslySetInnerHTML={{ __html: chapter.content }} />

        {chapter.end_note && (
          <aside className="reader__note">
            <p className="reader__note-label">End Note</p>
            <div dangerouslySetInnerHTML={{ __html: chapter.end_note }} />
          </aside>
        )}
      </article>

      <nav className="reader-nav">
        <button className="reader-nav__btn" disabled={!hasPrev}
          onClick={() => goChapter(num - 1)}>← Previous</button>
        <Link href={`/story/${storyId}`} className="reader-nav__index">All chapters</Link>
        <button className="reader-nav__btn" disabled={!hasNext}
          onClick={() => goChapter(num + 1)}>Next →</button>
      </nav>

      <p className="reader-hint">Keys: ← → chapters · + − text size · t theme. Line spacing, serif/sans &amp; column width: buttons above.</p>
    </div>
  )
}
