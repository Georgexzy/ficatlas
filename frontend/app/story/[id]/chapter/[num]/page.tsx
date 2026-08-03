"use client"
import { useEffect, useRef, useState } from "react"
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
  language?: string
  chapters: { number: number; title?: string }[]
}

// Stored languages are display names in their own script ("Русский", not "ru"),
// because that is how AO3 records them. A screen reader needs a BCP-47 code on
// the element or it reads the whole chapter with English pronunciation rules —
// and a large share of this index is not English. Names here mirror the
// canonical keys in backend/language_aliases.py.
const LANG_CODES: Record<string, string> = {
  English: "en", Chinese: "zh", Spanish: "es", Russian: "ru", French: "fr",
  Portuguese: "pt", Indonesian: "id", German: "de", Italian: "it",
  Ukrainian: "uk", Polish: "pl", Filipino: "fil", Vietnamese: "vi",
  Czech: "cs", Turkish: "tr", Japanese: "ja", Hungarian: "hu", Korean: "ko",
  Thai: "th", Swedish: "sv", Finnish: "fi", Dutch: "nl", Norwegian: "no",
  Danish: "da", Belarusian: "be", Hebrew: "he", Esperanto: "eo", Arabic: "ar",
  Greek: "el", Romanian: "ro", Bulgarian: "bg", Croatian: "hr",
  Serbian: "sr", Catalan: "ca", Latin: "la", Persian: "fa", Hindi: "hi",
  "中文-普通话 國語": "zh", "Español": "es", "Français": "fr", "日本語": "ja",
  "Português brasileiro": "pt-BR", "Deutsch": "de", "Italiano": "it",
  "Polski": "pl", "Українська": "uk", "한국어": "ko", "Tiếng Việt": "vi",
}

// Right-to-left scripts need dir="rtl" or the text renders in the wrong order.
const RTL = new Set(["ar", "he", "fa", "ur"])

// 250 wpm is the usual middle of the range quoted for adult prose reading.
const WORDS_PER_MINUTE = 250

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
  const [justify, setJustify] = useState(false)
  const [scrollPct, setScrollPct] = useState(0)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const headingRef = useRef<HTMLHeadingElement | null>(null)

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
    setJustify(localStorage.getItem("ficatlas:reader_justify") === "1")

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
    localStorage.setItem("ficatlas:reader_justify", justify ? "1" : "0")
  }, [justify])

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
      const prev = progress[story.id] || {}
      // scrollPct belongs to the chapter it was measured in, so it must NOT be
      // carried over by the spread when the chapter changes.
      //
      // Clicking "Next" happens at the bottom of a chapter, where scrollPct is
      // ~1. The spread kept that value while overwriting `chapter` with the new
      // number, so the restore effect below then matched — same story, same
      // chapter number, a saved position of ~1 — and dropped the reader at the
      // BOTTOM of the chapter they had just opened.
      const sameChapter = prev.chapter === chapter.number
      progress[story.id] = {
        ...prev,
        chapter: chapter.number,
        scrollPct: sameChapter ? prev.scrollPct : 0,
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
      const resume = saved && saved.chapter === chapter.number && saved.scrollPct > 0.01
      // Wait one paint so the content has laid out before moving the viewport.
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const h = document.documentElement
        if (resume) {
          window.scrollTo({ top: (h.scrollHeight - h.clientHeight) * saved.scrollPct,
                            behavior: "auto" })
        } else {
          // Explicitly go to the top. Client-side routing keeps the document
          // alive between chapters, so without this the viewport stays wherever
          // the previous chapter was left — which is the bottom, because that
          // is where the "Next" button is.
          window.scrollTo({ top: 0, behavior: "auto" })
        }
        // Focus the heading so a keyboard or screen-reader user is moved to the
        // new chapter too, rather than being left on the old page's Next button.
        headingRef.current?.focus?.()
      }))
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
      // Any editable target, not just <input> — a textarea or contenteditable
      // otherwise had "-" shrink the text and the arrows change chapter
      // mid-sentence. Modifier chords belong to the browser, not to us.
      const t = e.target as HTMLElement | null
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return
      if (e.ctrlKey || e.metaKey || e.altKey) return
      // Escape closes the settings sheet rather than falling through to the
      // chapter shortcuts underneath it.
      if (e.key === "Escape") { setSettingsOpen(false); return }
      if (e.key === "ArrowLeft" && num > 1) goChapter(num - 1)
      if (e.key === "ArrowRight" && story && num < story.chapter_count) goChapter(num + 1)
      if (e.key === "+" || e.key === "=") setFontSize(s => Math.min(s + 1, 24))
      if (e.key === "-") setFontSize(s => Math.max(s - 1, 13))
      if (e.key === "t") setTheme(t => t === "default" ? "sepia" : t === "sepia" ? "dark" : "default")
      if (e.key === "j") setJustify(j => !j)
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [num, story, storyId, router])

  if (!chapter || !story) {
    return (
      <div className="reader-shell">
        <p className="loading" role="status" aria-live="polite">Loading…</p>
      </div>
    )
  }

  const hasPrev = num > 1
  const hasNext = num < story.chapter_count
  const langCode = LANG_CODES[(story.language || "English").trim()] || "en"
  const dir = RTL.has(langCode) ? "rtl" : undefined
  const minutes = Math.max(1, Math.round((chapter.word_count || 0) / WORDS_PER_MINUTE))

  return (
    <div className="reader-shell" data-width={width} data-font={fontFamily} data-theme={theme}>
      {/* Keyboard users land on the toolbar first and would otherwise tab through
          every control before reaching a word of the story. */}
      <a href="#reader-body" className="skip-link">Skip to chapter text</a>
      <div className="reader-progress" style={{ width: `${scrollPct}%` }}
        role="progressbar" aria-label="Reading progress"
        aria-valuenow={Math.round(scrollPct)} aria-valuemin={0} aria-valuemax={100} />
      <div className="reader-topbar reader-topbar--sticky">
        <Link href={`/story/${storyId}`} className="back-link">← {story.title}</Link>
        {/* One "Aa" button instead of eight controls, the way Apple Books does
            it. On a 390px phone the inline toolbar wrapped to THREE rows, so
            eight buttons stood between the reader and the first line of prose.
            The panel below holds the same settings and is a bottom sheet on a
            phone, a popover on a desktop. */}
        <div className="reader-controls">
          <button
            className={`reader-ctrl reader-ctrl--aa ${settingsOpen ? "is-on" : ""}`}
            onClick={() => setSettingsOpen(o => !o)}
            aria-expanded={settingsOpen}
            aria-label="Reading settings: type, size, spacing and colour"
            title="Reading settings"
          >Aa</button>
        </div>
      </div>

      {settingsOpen && (
        <>
          <div className="reader-sheet__backdrop" onClick={() => setSettingsOpen(false)} />
          <div className="reader-sheet" role="dialog" aria-label="Reading settings">
            <div className="reader-sheet__head">
              <p className="reader-sheet__title">Reading settings</p>
              <button className="reader-sheet__close" onClick={() => setSettingsOpen(false)}
                aria-label="Close reading settings">✕</button>
            </div>

            <div className="reader-sheet__row">
              <span className="reader-sheet__label">Text size</span>
              <div className="reader-seg">
                <button onClick={() => setFontSize(v => Math.max(v - 1, 13))}
                  aria-label="Smaller text">A−</button>
                <span className="reader-seg__value">{fontSize}px</span>
                <button onClick={() => setFontSize(v => Math.min(v + 1, 24))}
                  aria-label="Larger text">A+</button>
              </div>
            </div>

            <div className="reader-sheet__row">
              <span className="reader-sheet__label">Line spacing</span>
              <div className="reader-seg">
                <button onClick={() => setLineHeight(l => Math.max(1.3, +(l - 0.1).toFixed(1)))}
                  aria-label="Tighter line spacing">↕−</button>
                <span className="reader-seg__value">{lineHeight.toFixed(1)}</span>
                <button onClick={() => setLineHeight(l => Math.min(2.4, +(l + 0.1).toFixed(1)))}
                  aria-label="Looser line spacing">↕+</button>
              </div>
            </div>

            <div className="reader-sheet__row">
              <span className="reader-sheet__label">Typeface</span>
              <div className="reader-seg reader-seg--choice">
                {(["serif", "sans"] as const).map(f => (
                  <button key={f} onClick={() => setFontFamily(f)}
                    className={fontFamily === f ? "is-on" : ""}
                    aria-pressed={fontFamily === f}>
                    {f === "serif" ? "Serif" : "Sans"}
                  </button>
                ))}
              </div>
            </div>

            <div className="reader-sheet__row">
              <span className="reader-sheet__label">Theme</span>
              <div className="reader-seg reader-seg--choice">
                {(["default", "sepia", "dark"] as const).map(t => (
                  <button key={t} onClick={() => setTheme(t)}
                    className={theme === t ? "is-on" : ""}
                    aria-pressed={theme === t}>
                    {t === "default" ? "Light" : t === "sepia" ? "Sepia" : "Dark"}
                  </button>
                ))}
              </div>
            </div>

            <div className="reader-sheet__row">
              <span className="reader-sheet__label">Column</span>
              <div className="reader-seg reader-seg--choice">
                {(["narrow", "wide"] as const).map(w => (
                  <button key={w} onClick={() => setWidth(w)}
                    className={width === w ? "is-on" : ""}
                    aria-pressed={width === w}>
                    {w === "narrow" ? "Narrow" : "Wide"}
                  </button>
                ))}
              </div>
            </div>

            <div className="reader-sheet__row">
              <span className="reader-sheet__label">Alignment</span>
              <div className="reader-seg reader-seg--choice">
                <button onClick={() => setJustify(false)} className={!justify ? "is-on" : ""}
                  aria-pressed={!justify}>Ragged</button>
                <button onClick={() => setJustify(true)} className={justify ? "is-on" : ""}
                  aria-pressed={justify}>Justified</button>
              </div>
            </div>

            <p className="reader-sheet__hint">
              Keys: <kbd>←</kbd> <kbd>→</kbd> chapters · <kbd>+</kbd> <kbd>−</kbd> size ·
              <kbd>t</kbd> theme · <kbd>j</kbd> justify
            </p>
          </div>
        </>
      )}

      {/* Floating exit button — exit reader from anywhere without scrolling up */}
      <Link href={`/story/${storyId}`} className="reader-fab" title="Back to story page" aria-label="Exit reader">
        ✕
      </Link>

      {/* fontSize is expressed in rem, not px. A px size ignores the reader's
          own browser font-size setting entirely, which is the setting people
          with low vision actually rely on; rem scales with it and still honours
          the in-app A+/A- buttons on top. */}
      <article className="reader" data-width={width} data-font={fontFamily}
        data-justify={justify ? "on" : "off"}
        lang={langCode} dir={dir}
        style={{ fontSize: `${(fontSize / 16).toFixed(3)}rem`, lineHeight }}>
        <header className="reader__header">
          <p className="reader__breadcrumb">
            Chapter {num} of {story.chapter_count}
            <span className="dot"> · </span>
            <span>{minutes} min read</span>
          </p>
          <h1 className="reader__title" tabIndex={-1} ref={headingRef}>{chapter.title || `Chapter ${num}`}</h1>
          {chapter.summary && <p className="reader__summary">{chapter.summary}</p>}
        </header>

        {chapter.start_note && (
          <aside className="reader__note">
            <p className="reader__note-label">Author's Note</p>
            <div dangerouslySetInnerHTML={{ __html: chapter.start_note }} />
          </aside>
        )}

        <div id="reader-body" className="reader__body reader__content"
          dangerouslySetInnerHTML={{ __html: chapter.content }} />

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

      <p className="reader-hint">Keys: ← → chapters · + − text size · t theme · j justify. Line spacing, serif/sans &amp; column width: buttons above.</p>
    </div>
  )
}
