"use client"
import { memo, useCallback, useEffect, useRef, useState } from "react"
import { useParams, useRouter } from "next/navigation"
import Link from "next/link"
import { describeError, type Failure } from "@/lib/errors"
import { useAuth } from "@/lib/auth"
import { navigateTo } from "@/lib/navigation"
import { localFirst } from "@/lib/localFirst"
import { fetchWithTimeout, USER_TIMEOUT_MS, BACKGROUND_TIMEOUT_MS } from "@/lib/net"
// A STATIC import, and that is load-bearing.
//
// This was `await import("@/lib/offline")` inside the offline read — and a
// dynamic import is a network fetch for a JS chunk. So the one code path whose
// entire purpose is to work without a network could not start until the network
// handed over a file. On a cold navigation over a dead-but-associated
// connection, with the browser's ~6 connection slots already held by requests
// that will never settle, that import never resolved: the reader sat on
// "Loading…" and then "Couldn\u2019t load this chapter", over text sitting in
// IndexedDB. Measured: chapter 1 in 0.6s, then every chapter after it failed.
//
// Nothing on the offline path may be lazily loaded. The saving here was one
// small chunk on a route that is the reason the chunk exists.
import { getOfflineStory, type OfflineStory } from "@/lib/offline"
import ReadAloud from "./ReadAloud"

const API_BASE = ""  // relative — handled by Next.js rewrite to backend

interface ChapterFull {
  id: string; number: number; title?: string; summary?: string;
  content: string; word_count: number; posted_at?: string;
  start_note?: string; end_note?: string
}

interface StoryMin {
  id: string; title: string; author: string; chapter_count: number;
  chapter_count_total?: number
  language?: string
  characters?: string[]
  relationships?: string[]
  tags?: string[]
  warnings?: string[]
  site?: string
  url?: string
  summary?: string
  word_count?: number
  chapters: { number: number; title?: string }[]
}

interface SimilarCard {
  id: string; title: string; author: string;
  word_count: number; relationships?: string[]; fandoms?: string[];
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

// Next-chapter prefetch.
//
// The route was already prefetched (router.prefetch below), but that only warms
// the Next.js bundle for /story/[id]/chapter/[num] — the chapter TEXT is fetched
// by this component after it mounts. So "next chapter", which is the single most
// repeated action anyone performs here, still paid a full round trip and showed
// the loading state every time, on a page whose JS was already sitting ready.
//
// Warming the body too makes the common case instant. Deliberately small:
//   * one chapter ahead only — reading forward is the pattern; hoarding more
//     would spend a phone's data on chapters most readers never reach
//   * a 2-entry cache, so going next → back → next does not refetch
//   * failures are swallowed. A prefetch that fails must be invisible; the real
//     load path runs again and owns the error reporting
const CHAPTER_CACHE = new Map<string, any>()
const CACHE_MAX = 2

function cacheKey(storyId: string, num: number) { return `${storyId}/${num}` }

function cachePut(key: string, value: any) {
  if (CHAPTER_CACHE.has(key)) CHAPTER_CACHE.delete(key)
  CHAPTER_CACHE.set(key, value)
  while (CHAPTER_CACHE.size > CACHE_MAX) {
    const oldest = CHAPTER_CACHE.keys().next()
    if (oldest.done) break
    CHAPTER_CACHE.delete(oldest.value)
  }
}

// The story record behind a chapter this device already holds, built in ONE
// place.
//
// It was inlined in the chapter load, and the entire-work reader needs exactly
// the same object. Two copies of this mapping would be two chances to drop a
// field again — `language` and `chapter_count_total` were both once missing
// here, which typeset every offline chapter of a non-English work as English
// and printed "Chapter 3 of 4" on a 199-chapter novel.
function offlineStoryMin(saved: OfflineStory): StoryMin {
  return {
    id: saved.id, title: saved.title, author: saved.author,
    // How many this device HOLDS, which is not how long the work is. Both of
    // these fields were already on the record and neither was read, so an
    // offline chapter of a 199-chapter novel carried the breadcrumb
    // "Chapter 3 of 4" — a number that is not merely wrong but reads as a
    // complete four-chapter work, and sends a reader looking for chapter 5.
    chapter_count: saved.chapters.length,
    chapter_count_total: saved.chapter_count_total ?? saved.chapters.length,
    // Dropped here before, so every offline chapter of a non-English work
    // was announced and typeset as English. It is stored; it was simply
    // never carried across.
    language: saved.language,
    // The cast and pairings are server-rendered into the story page, not the
    // chapter page, so nothing here needs them — but the record has them and
    // passing them through costs nothing.
    characters: saved.characters,
    relationships: saved.relationships,
    tags: saved.tags,
    warnings: saved.warnings,
    site: saved.site,
    url: saved.url,
    summary: saved.summary,
    word_count: saved.word_count,
    chapters: saved.chapters.map(c => ({ number: c.number, title: c.title })),
  } as any
}

// One chapter of the entire-work view, and it is memoised on purpose.
//
// The reader re-renders on every scrolled frame — it owns the progress bar —
// and a long work is 199 of these sections, each carrying a chapter's whole
// HTML. Mapping them inline would have React reconcile that subtree at frame
// rate on a phone. Memo makes a scroll pass find the same chapter object and
// stop there; `onRef` has to be a stable callback for that to hold, which is
// what `setSectionRef` below is.
const ReaderPart = memo(function ReaderPart({
  ch, totalLabel, onRef,
}: {
  ch: ChapterFull
  totalLabel: string | number
  onRef: (n: number, el: HTMLElement | null) => void
}) {
  return (
    <section className="reader__part" id={`ch-${ch.number}`}
      ref={el => onRef(ch.number, el)}>
      <h2 className="reader__part-title" tabIndex={-1}>
        <span className="reader__part-num">Chapter {ch.number} of {totalLabel}</span>
        {/* Only when there is a real one: the num line above already says
            "Chapter 3 of 199", and repeating it as the title too is how an
            unlabelled chapter reads. */}
        {ch.title && <span className="reader__part-name">{ch.title}</span>}
      </h2>
      {ch.summary && <p className="reader__summary">{ch.summary}</p>}
      {ch.start_note && (
        <aside className="reader__note">
          <p className="reader__note-label">Author's Note</p>
          <div dangerouslySetInnerHTML={{ __html: ch.start_note }} />
        </aside>
      )}
      <div className="reader__body reader__content"
        dangerouslySetInnerHTML={{ __html: ch.content }} />
      {ch.end_note && (
        <aside className="reader__note">
          <p className="reader__note-label">End Note</p>
          <div dangerouslySetInnerHTML={{ __html: ch.end_note }} />
        </aside>
      )}
    </section>
  )
})


// Which chapter the viewport is in, and how far through it — asked of the
// section map rather than of the scroll percentage.
//
// A percentage of the whole document cannot answer this on the entire-work
// view: the document grows as pages arrive, so 12% is twelve percent of one
// page before the rest land and twelve percent of the whole work after. The
// section under the top bar does not move when something is appended below it,
// which is the property every consumer here needs.
function whereWeAre(sections: Map<number, HTMLElement>):
    { ch: number; frac: number } | null {
  // SORTED, not insertion order: a ref callback re-registering a section moves
  // it to the end of the Map, and this reads order.
  const seen = [...sections.keys()].sort((a, b) => a - b)
  let ch: number | null = null
  for (const n of seen) {
    const el = sections.get(n)
    if (el && el.getBoundingClientRect().top <= READ_LINE) ch = n
    else break
  }
  // Above the first heading, in the title block: name the first chapter rather
  // than returning nothing.
  if (ch == null) {
    if (!seen.length) return null
    ch = seen[0]
  }
  const r = sections.get(ch)!.getBoundingClientRect()
  // Measured to the reading line, not to the viewport edge: at a section's own
  // start the reader is AT its heading, which is frac 0 — a fraction taken from
  // y=0 would start them one toolbar-height into the chapter instead.
  return { ch, frac: r.height > 0
    ? Math.min(1, Math.max(0, (READ_LINE - r.top) / r.height))
    : 0 }
}

// Where the reading line is, in pixels from the top of the viewport — just
// under the sticky toolbar, which is a wrapped two-line back-link plus its
// 20px margin on a phone and a single line on a desktop.
//
// Every question the entire-work view asks about position uses this one number:
// which chapter is current (a section counts when its heading is at or above
// it), how far through a chapter the reader is (measured to it, not to the raw
// viewport edge, so the heading and the first line of text are treated alike),
// and where a jump or a restore leaves the page (its heading lands on it).
// Three constants would drift, and the symptom would be a breadcrumb naming a
// different chapter from the one the heading is sitting under.
//
// The position itself is NOT stored separately: `writeProgress` already keeps a
// percentage keyed by chapter under `ficatlas:progress`, which is what the story
// page and the library read, and an entire-work reading position is the same
// thing — a chapter, and how far through it. A second record would be a second
// place for "Continue" to disagree with.
const READ_LINE = 64


export default function ChapterPage() {
  const params = useParams()
  const router = useRouter()
  const storyId = params?.id as string
  // Which view this is, taken from the SHAPE of the URL rather than from a
  // prop: `/story/[id]/chapter/[num]` always has a number, `/story/[id]/entire`
  // never does, and both render this component (entire/page.tsx imports it, so
  // the two readers cannot drift apart).
  //
  // Not a prop because Next.js generates a `PageProps` constraint over each
  // route file's default export and refuses any bespoke props type on it —
  // a page component takes the framework's props or none. Gated on `storyId`
  // because `useParams()` can hand back nothing during a navigation: without
  // that, a chapter route would briefly read as an entire work and fetch one.
  const entire = Boolean(storyId) && params?.num === undefined
  // NaN in entire-work mode, where there is no chapter number in the URL — and
  // every consumer below already guards on `!num`, so the single-chapter
  // effects simply stand down rather than needing an `if (entire)` each. The
  // one place it is READ is the breadcrumb, which whole mode replaces.
  const num = Number(params?.num)
  // Only for the error screen: whether to offer a sign-in when a chapter is
  // refused. Nothing else here depends on who is reading.
  const { user } = useAuth()
  const [chapter, setChapter] = useState<ChapterFull | null>(null)
  const [story, setStory] = useState<StoryMin | null>(null)
  const [loadError, setLoadError] = useState<Failure | null>(null)
  // Whether the network is ANSWERING, as observed on this page's own request —
  // not as `navigator.onLine` claims. Nothing may be prefetched on the strength
  // of a flag that reads `true` on a connection carrying no packets; see the
  // prefetch effect below for what that cost.
  const [networkAlive, setNetworkAlive] = useState(false)
  // Bumped to re-run the chapter load without a navigation — used by the Retry
  // button and by coming back online.
  const [reloadKey, setReloadKey] = useState(0)
  const [fontSize, setFontSize] = useState(17)
  const [fontFamily, setFontFamily] = useState<"serif" | "sans">("serif")
  const [width, setWidth] = useState<"narrow" | "wide">("narrow")
  const [lineHeight, setLineHeight] = useState(1.7)
  // "site" follows whatever the site theme is set to, and is the default.
  //
  // The reader used to keep an entirely separate theme, so someone who set the
  // site to dark still opened a story onto a white page — the one screen where
  // that matters most, at the one time of day people read. Sepia stays, because
  // it is a reading preference rather than a lighting one and has no site-wide
  // equivalent.
  const [theme, setTheme] = useState<"site" | "default" | "sepia" | "dark">("site")
  // Mirrors the <html data-theme> the site toggle writes, so choosing "site"
  // tracks a later change without a reload.
  const [siteTheme, setSiteTheme] = useState<"default" | "dark">("dark")
  useEffect(() => {
    const read = () =>
      setSiteTheme(document.documentElement.getAttribute("data-theme") === "light"
        ? "default" : "dark")
    read()
    const obs = new MutationObserver(read)
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] })
    return () => obs.disconnect()
  }, [])
  const [justify, setJustify] = useState(false)
  const [scrollPct, setScrollPct] = useState(0)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  // True while the reader is being placed at the start of a chapter, so the
  // position-saving listener ignores the scrolls that placement causes.
  const restoringRef = useRef(false)
  // Which story the entire-work view has already placed itself for, so the
  // restore below runs once rather than on every page that appends to it.
  const restoredRef = useRef<string | null>(null)
  const [similar, setSimilar] = useState<SimilarCard[]>([])
  const [similarFor, setSimilarFor] = useState<string | null>(null)

  // ── Entire-work mode (AO3's "Entire Work") ────────────────────────────────
  //
  // One continuous page instead of one chapter per navigation. The text arrives
  // in pages — the endpoint cannot hand a 10.4MB work over in one response —
  // so `entireChapters` grows while it loads and `entireNext` says whether there
  // is another page.
  //
  // `entireNext` is deliberately tri-state: `undefined` is "we have not asked
  // yet", a number is "there is a page after this one, start at that chapter",
  // `null` is "that is the whole work". A boolean could not tell "not started"
  // from "finished", and both look like "do not fetch".
  const [entireChapters, setEntireChapters] = useState<ChapterFull[]>([])
  const [entireNext, setEntireNext] = useState<number | null | undefined>(undefined)
  // Which chapter the viewport is sitting in, so the breadcrumb, the jump list
  // and ← → all know where the reader is on a page that holds the whole work.
  const [currentCh, setCurrentCh] = useState<number | null>(null)
  // Set when a LATER page of the work fails to arrive. Deliberately not
  // `loadError`: that one takes over the screen, and blanking a work already
  // showing three hundred kilobytes of text because page four timed out would
  // be the reader failing in the moment it is most in use.
  const [entireStalled, setEntireStalled] = useState(false)
  // Bumped by "Load the rest" so the fetch effect below re-runs without the
  // page number having to change.
  const [entireRetry, setEntireRetry] = useState(0)
  // number -> its <section>, for jumping and for the position restore. A Map
  // rather than an array of refs because it stays correct when chapters append
  // in any order and when one unmounts.
  const sectionsRef = useRef<Map<number, HTMLElement>>(new Map())
  // Each chapter gets the same callback identity for its whole life, which is
  // what lets `ReaderPart` be memoised and what keeps React from calling
  // ref(null)/ref(el) on every frame of a scroll.
  const setSectionRef = useCallback((n: number, el: HTMLElement | null) => {
    if (el) sectionsRef.current.set(n, el)
    else sectionsRef.current.delete(n)
  }, [])

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
      fetchWithTimeout(`${API_BASE}/api/settings`, {}, BACKGROUND_TIMEOUT_MS).then(r => r.json()).then(s => {
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

  // Reading progress bar, and — in entire-work mode — which chapter the
  // viewport is sitting in.
  //
  // Throttled to one update per animation frame. `scroll` fires more often than
  // frames get painted and each of these is a React render: on the
  // single-chapter reader that was a handful of nodes being reconciled, and on
  // a whole work it is every section in it, which measured as a visible stutter
  // on a phone before the throttle went in.
  useEffect(() => {
    let raf = 0
    const onScroll = () => {
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = 0
        const h = document.documentElement
        const denom = h.scrollHeight - h.clientHeight
        setScrollPct(Math.min(100, Math.max(0, denom > 0 ? (h.scrollTop / denom) * 100 : 0)))

        if (!entire || !sectionsRef.current.size) return
        // One question, one implementation: the save and the restore below ask
        // this same helper where the reader is, and a second copy of the rule
        // would eventually disagree with this one.
        const found = whereWeAre(sectionsRef.current)?.ch ?? null
        setCurrentCh(c => (c === found ? c : found))
      })
    }
    window.addEventListener("scroll", onScroll, { passive: true })
    return () => {
      window.removeEventListener("scroll", onScroll)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [entire])

  // Loading a chapter.
  //
  // Three things were wrong here and they compounded:
  //
  //   * `chapter` was never cleared when `num` changed, so between clicking Next
  //     and the response arriving the page showed the PREVIOUS chapter's text
  //     under the new chapter's heading ("Chapter 4 of 20" above chapter 3), at
  //     whatever scroll position the old chapter was left at — which, having just
  //     used the Next button, is the bottom.
  //   * there was no abort, so a slow response for a chapter you had already
  //     navigated away from could still land and replace the one you were reading.
  //   * there was no timeout, so an unreachable backend meant "Loading…" forever
  //     rather than falling through to the offline copy.
  //
  // Failure is also classified now. Every failure used to render "Unavailable
  // offline", including a plain 404 on a chapter that does not exist — telling
  // someone with a perfectly good connection to check their connection.
  // LOCAL FIRST. What this device holds is shown before the network is asked,
  // never after it has given up.
  //
  // This used to read `if (!navigator.onLine && await fromOffline()) return`,
  // and that flag reports whether the machine has a network INTERFACE, not
  // whether packets arrive. On a dead-but-associated connection — wifi with no
  // route, a captive portal, one bar of cell that cannot carry data — it is
  // `true`, so the shortcut to the saved copy never fired in the one case it
  // existed for. Two doomed fetches were raced instead and the saved text was
  // reached only when the 20-second abort timer gave up. Measured against a
  // chapter already in IndexedDB:
  //
  //     radio genuinely off   chapter shown in   <3s
  //     dead but associated   chapter shown in    21s
  //
  // Twenty-one seconds, over text readable in single-digit milliseconds. That
  // is what "offline reading doesn't work" was. See lib/localFirst.ts.
  useEffect(() => {
    if (!storyId || !num) return
    let cancelled = false

    setChapter(null)
    setLoadError(null)

    // Reads the saved copy, and has one deliberate side effect: it sets `story`
    // even when THIS chapter is not among the saved ones, so the error screen
    // can list the chapters the device does hold. Returning null means "this
    // chapter is not here", not "this story is unknown".
    const readSaved = async () => {
      const saved = await getOfflineStory(storyId)
      if (!saved || cancelled) return null
      const ch = saved.chapters.find(c => c.number === Number(num))
      setStory(offlineStoryMin(saved))
      if (!ch) return null
      return {
        story: null,
        chapter: {
          number: ch.number, title: ch.title, content: ch.content,
          start_note: ch.start_note, end_note: ch.end_note, summary: ch.summary,
        } as ChapterFull,
      }
    }

    localFirst<{ story: StoryMin | null; chapter: ChapterFull }>({
      local: readSaved,
      remote: async (signal) => {
        // A chapter warmed by the prefetch below is already in memory, so the
        // usual next-chapter tap renders without a round trip or a spinner.
        const warm = CHAPTER_CACHE.get(cacheKey(storyId, num))
        const [story, chapter] = await Promise.all([
          fetchWithTimeout(`${API_BASE}/api/stories/${storyId}`,
            { signal }, USER_TIMEOUT_MS)
            .then(r => { if (!r.ok) throw describeError(null, r.status); return r.json() }),
          warm ?? fetchWithTimeout(`${API_BASE}/api/stories/${storyId}/chapters/${num}`,
            { signal }, USER_TIMEOUT_MS)
            .then(r => { if (!r.ok) throw describeError(null, r.status); return r.json() }),
        ])
        return { story, chapter }
      },
      onLocal: ({ chapter }) => setChapter(chapter),
      // The network record carries what the saved copy cannot — kudos, the full
      // chapter list, completion — so it replaces the local one when it lands.
      onRemote: ({ story, chapter }) => {
        setNetworkAlive(true)
        if (story) setStory(story)
        setChapter(chapter)
      },
      onFailure: (e: any) => { setNetworkAlive(false); setLoadError(e?.kind ? e : describeError(e)) },
      isCancelled: () => cancelled,
    })

    return () => { cancelled = true }
  }, [storyId, num, reloadKey])

  // Loading the WHOLE work. Same shape as the chapter load above — abortable,
  // timed out, LOCAL FIRST, failure classified — for the same reasons, and it
  // stands down when `entire` is false so a single-chapter read never pays for
  // it.
  useEffect(() => {
    if (!entire || !storyId) return
    let cancelled = false

    setEntireChapters([])
    setEntireNext(undefined)
    setEntireStalled(false)
    setCurrentCh(null)
    setLoadError(null)
    sectionsRef.current.clear()
    // A retry has to be able to place the reader again, or "Try again" would
    // re-fetch the work and leave it at the top of chapter one.
    restoredRef.current = null

    // The saved copy IS the whole work on this path, which is what makes "save
    // offline" and "read the entire thing" one feature rather than two: every
    // chapter the download finished with comes back at once, no paging to ask
    // for, in single-digit milliseconds from IndexedDB.
    const readSaved = async () => {
      const saved = await getOfflineStory(storyId)
      if (!saved || cancelled) return null
      setStory(offlineStoryMin(saved))
      const chs = [...saved.chapters].sort((a, b) => a.number - b.number)
      if (!chs.length) return null
      return {
        story: null,
        chapters: chs.map(c => ({
          number: c.number, title: c.title, content: c.content,
          start_note: c.start_note, end_note: c.end_note, summary: c.summary,
        } as ChapterFull)),
        // A saved copy is complete by construction: `downloadStoryForOffline`
        // downloads every chapter or throws, and the partial case is reported
        // by the story page as holes rather than served here as a short work.
        next: null as number | null,
      }
    }

    localFirst<{ story: StoryMin | null; chapters: ChapterFull[]; next: number | null }>({
      local: readSaved,
      remote: async (signal) => {
        // Metadata and page one together: the outline drives the jump list and
        // the breadcrumb, so waiting for it separately would render a work with
        // no idea how long it is.
        const [storyRes, pageRes] = await Promise.all([
          fetchWithTimeout(`${API_BASE}/api/stories/${storyId}`,
            { signal }, USER_TIMEOUT_MS),
          fetchWithTimeout(`${API_BASE}/api/stories/${storyId}/entire?after=0`,
            { signal }, USER_TIMEOUT_MS),
        ])
        if (!storyRes.ok) throw describeError(null, storyRes.status)
        if (!pageRes.ok) throw describeError(null, pageRes.status)
        const [story, page] = await Promise.all([storyRes.json(), pageRes.json()])
        return { story, chapters: page.chapters ?? [], next: page.next_after ?? null }
      },
      onLocal: ({ chapters, next }) => {
        setEntireChapters(chapters)
        setEntireNext(next)
      },
      // Replaces the saved copy wholesale — the network knows which chapters
      // exist and how they read today, and the continuation effect takes it
      // from the number this page ends at.
      onRemote: ({ story, chapters, next }) => {
        setNetworkAlive(true)
        if (story) setStory(story)
        setEntireChapters(chapters)
        setEntireNext(next)
        setEntireStalled(false)
      },
      onFailure: (e: any) => { setNetworkAlive(false); setLoadError(e?.kind ? e : describeError(e)) },
      isCancelled: () => cancelled,
    })

    return () => { cancelled = true }
  }, [entire, storyId, reloadKey])

  // The next page, fetched on its own and appended as it lands.
  //
  // Sequential rather than fanned out, and that is not a limitation to work
  // around: the endpoint picks page boundaries by BYTES rather than by chapter
  // count (a chapter can be half a megabyte; sixty can be 6.7MB), so the next
  // `after` is not knowable until the current page answers. Meanwhile the
  // reader is already reading page one — the alternative, one request for the
  // whole 10.4MB work, is a spinner over text that could have been on screen
  // seconds earlier.
  //
  // A page that will not arrive does NOT fail the view: `entireStalled` is set
  // and the reader is offered the rest again, with the work already showing
  // left exactly where it was.
  useEffect(() => {
    if (!entire || !storyId || typeof entireNext !== "number") return
    const from = entireNext
    const ac = new AbortController()
    let active = true
    fetchWithTimeout(`${API_BASE}/api/stories/${storyId}/entire?after=${from}`,
      { signal: ac.signal }, USER_TIMEOUT_MS)
      .then(r => { if (!r.ok) throw describeError(null, r.status); return r.json() })
      .then(page => {
        if (!active || !Array.isArray(page?.chapters)) return
        setEntireChapters(prev => [...prev, ...page.chapters])
        setEntireNext(page.next_after ?? null)
        setEntireStalled(false)
      })
      .catch(() => { if (active) setEntireStalled(true) })
    return () => { active = false; ac.abort() }
  }, [entire, storyId, entireNext, entireRetry])

  // Paint the reader's page colour onto the ROOT element while the reader is open.
  //
  // The reader themes paint their page with a fixed, full-viewport layer inside
  // the shell. That covers the layout viewport, and on a desktop that is the
  // whole story. On a phone it is not: as the address bar retracts and returns,
  // the browser reveals a band outside the layout viewport, and what shows there
  // is the CANVAS — which takes its colour from the root element, i.e. the site
  // theme. So a sepia page on a dark-themed site showed near-black slivers at
  // the top edge while scrolling.
  //
  // Set on BODY, not on <html>. The canvas takes its colour from the root
  // element, but when the root has no background of its own the body's is
  // propagated up to it — which is the arrangement here, since only `body` sets
  // one. Setting a background directly on <html> therefore breaks that
  // propagation: body stops being the canvas source and starts painting its own
  // background as an ordinary box over the whole viewport. Tried it, and the
  // reader went dark in every theme, because that box is the site's palette.
  // Setting body keeps the propagation and colours the canvas.
  useEffect(() => {
    const resolved = theme === "site" ? siteTheme : theme
    const colour = resolved === "sepia" ? "#f4ecd8"
                 : resolved === "dark" ? "#0e0e10"
                 : resolved === "default" ? "#fdfcf9"
                 : ""
    if (!colour) return
    const previous = document.body.style.backgroundColor
    document.body.style.backgroundColor = colour
    return () => { document.body.style.backgroundColor = previous }
  }, [theme, siteTheme])

  // Coming back online retries a chapter that failed to load — but only one that
  // failed. Retrying unconditionally would blank and re-fetch the chapter under
  // someone who is reading it perfectly happily from the saved copy, which is
  // the moment a flaky connection is least welcome to interrupt.
  useEffect(() => {
    if (!loadError) return
    const retry = () => setReloadKey(k => k + 1)
    window.addEventListener("online", retry)
    return () => window.removeEventListener("online", retry)
  }, [loadError])

  // Reading position is stored per chapter, under `positions`.
  //
  // There was one `scrollPct` for the whole story, which meant a position only
  // survived while you stayed on the chapter that recorded it. Going back a
  // chapter — the ordinary way to re-read the end of the last one — always
  // landed at the top, and every chapter change had to actively defend against
  // the previous chapter's percentage being reapplied to the new one. Keying the
  // position by chapter number removes both problems: nothing to carry over, and
  // nothing to clear.
  //
  // `chapter`, `scrollPct`, `totalChapters`, `title` and `author` are still
  // written at the top level because the story page and the library read them.
  // `forCh` exists for the entire-work reader, which reads a whole book at once
  // and so has no `chapter` of its own to point at — but whose position IS a
  // position in some chapter, exactly as this record already models it. Passing
  // the chapter in keeps ONE coordinate system for both readers rather than a
  // second record the story page and the library would each have to learn: a
  // reader who works through an entire work still gets "Continue Chapter 112"
  // where they actually were.
  const writeProgress = useCallback((pct: number | null, forCh?: number) => {
    if (!story) return
    const n = forCh ?? chapter?.number
    if (n == null) return
    try {
      const all = JSON.parse(localStorage.getItem("ficatlas:progress") ?? "{}")
      const prev = all[story.id] || {}
      const positions = { ...(prev.positions || {}) }
      if (pct != null) positions[n] = Math.min(1, Math.max(0, pct))
      all[story.id] = {
        ...prev,
        chapter: n,
        positions,
        scrollPct: positions[n] ?? 0,
        totalChapters: story.chapter_count,
        title: story.title,
        author: story.author,
        at: new Date().toISOString(),
      }
      localStorage.setItem("ficatlas:progress", JSON.stringify(all))
    } catch {}
  }, [story, chapter])

  // Mark the chapter as the one being read as soon as it opens.
  useEffect(() => { writeProgress(null) }, [writeProgress])

  // Persist scroll position within the chapter (debounced).
  useEffect(() => {
    if (!story || !chapter) return
    let timer: ReturnType<typeof setTimeout> | null = null
    const save = () => {
      const h = document.documentElement
      const denom = h.scrollHeight - h.clientHeight
      writeProgress(denom > 0 ? h.scrollTop / denom : 0)
    }
    const onScroll = () => {
      // Ignore scrolls we caused ourselves while placing the reader — otherwise
      // the settle loop below writes its own intermediate positions back.
      if (restoringRef.current) return
      if (timer) clearTimeout(timer)
      timer = setTimeout(save, 600)
    }
    window.addEventListener("scroll", onScroll, { passive: true })
    // A tab closed or backgrounded mid-chapter used to lose up to 600ms of
    // reading position; on a phone, backgrounding is how you leave.
    // Only a PENDING save is flushed. Saving unconditionally on every
    // visibility change meant a tab left open on an old chapter — on another
    // device, or behind other tabs — re-asserted "reading chapter 22, now"
    // just by being looked at, and the newer timestamp won the sync against
    // the chapter the reader had actually reached (61, on the account that
    // found this). A tab nobody scrolled has nothing new to say.
    const flush = () => { if (timer) { clearTimeout(timer); timer = null; save() } }
    window.addEventListener("pagehide", flush)
    document.addEventListener("visibilitychange", flush)
    return () => {
      window.removeEventListener("scroll", onScroll)
      window.removeEventListener("pagehide", flush)
      document.removeEventListener("visibilitychange", flush)
      if (timer) clearTimeout(timer)
    }
  }, [story, chapter, writeProgress])

  // Place the reader once the chapter has rendered.
  //
  // This is the fix for landing at the BOTTOM of a freshly opened chapter. Three
  // things could put it there and all three are handled:
  //
  //   * `behavior: "auto"` does not mean "jump". It means "use the CSS value",
  //     and globals.css sets `html { scroll-behavior: smooth }` — so this was an
  //     animated scroll from wherever the previous chapter left the viewport.
  //     Any touch or wheel event cancels an animated scroll, so a reader who so
  //     much as brushed the screen stayed at the bottom. "instant" is explicit.
  //   * one measurement, two frames after the content commits, is taken before
  //     images and embedded media have laid out. The document then grows under
  //     the reader. Re-asserting the target over a short window survives that.
  //   * the browser restores its own scroll position on back/forward and does it
  //     asynchronously, so it can land after our single attempt. Same fix.
  useEffect(() => {
    if (!story || !chapter) return
    let target = 0
    try {
      const all = JSON.parse(localStorage.getItem("ficatlas:progress") ?? "{}")
      const pct = all[story.id]?.positions?.[chapter.number]
      if (typeof pct === "number" && pct > 0.01) target = pct
    } catch {}

    restoringRef.current = true
    let frame = 0
    let raf = 0
    const place = () => {
      // A frame may already be queued when the reader takes over; without this
      // the loop gets one last move in and yanks them back.
      if (!restoringRef.current) return
      const h = document.documentElement
      const denom = h.scrollHeight - h.clientHeight
      const top = target > 0 ? denom * target : 0
      if (Math.abs(h.scrollTop - top) > 1) {
        window.scrollTo({ top, behavior: "instant" as ScrollBehavior })
      }
      // ~500ms of re-asserting at frame rate. Long enough for images to arrive
      // and for the browser's own restoration to have had its say; short enough
      // that it never fights a reader who has started scrolling.
      if (++frame < 30) raf = requestAnimationFrame(place)
      else restoringRef.current = false
    }
    raf = requestAnimationFrame(place)

    // Focus the heading so a keyboard or screen-reader user is moved to the new
    // chapter too, rather than being left on the old page's Next button.
    // preventScroll, or focusing it would itself scroll the heading into view.
    headingRef.current?.focus?.({ preventScroll: true })

    // A reader who scrolls deliberately owns the viewport from that moment on.
    const yield_ = () => { restoringRef.current = false; frame = 30 }
    window.addEventListener("wheel", yield_, { passive: true, once: true })
    window.addEventListener("touchmove", yield_, { passive: true, once: true })
    window.addEventListener("keydown", yield_, { once: true })

    return () => {
      cancelAnimationFrame(raf)
      restoringRef.current = false
      window.removeEventListener("wheel", yield_)
      window.removeEventListener("touchmove", yield_)
      window.removeEventListener("keydown", yield_)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chapter?.number, story?.id])

  // Persist where the entire-work reader was, debounced exactly like the
  // per-chapter save above — same 600ms, same flush on pagehide and
  // visibilitychange, because a tab closed mid-read is how people leave a
  // story on a phone.
  //
  // Asked of `whereWeAre` AT SAVE TIME rather than read out of `currentCh`:
  // that state is only ever updated inside an animation frame, so a pending
  // save would be lost whenever the effect re-registered — while the section
  // under the reading line is a fact about the document, costs one layout read
  // at this frequency, and cannot be stale by a frame.
  useEffect(() => {
    if (!entire || !story || !entireChapters.length) return
    let timer: ReturnType<typeof setTimeout> | null = null
    const save = () => {
      const here = whereWeAre(sectionsRef.current)
      if (!here) return
      // Into the same record the chapter reader writes, naming the chapter
      // this position belongs to — so "Continue" on the story page follows a
      // reader who read the book this way round.
      writeProgress(here.frac, here.ch)
    }
    const onScroll = () => {
      // Scrolls the placement loop caused are not a position the reader chose.
      if (restoringRef.current) return
      if (timer) clearTimeout(timer)
      timer = setTimeout(save, 600)
    }
    const flush = () => { if (timer) { clearTimeout(timer); timer = null; save() } }
    window.addEventListener("scroll", onScroll, { passive: true })
    window.addEventListener("pagehide", flush)
    document.addEventListener("visibilitychange", flush)
    return () => {
      window.removeEventListener("scroll", onScroll)
      window.removeEventListener("pagehide", flush)
      document.removeEventListener("visibilitychange", flush)
      if (timer) clearTimeout(timer)
    }
  }, [entire, story, entireChapters.length, writeProgress])

  // Place the entire-work reader where it was left.
  //
  // Restored by ANCHOR — the chapter, plus a fraction of THAT chapter — and not
  // by a percentage of the document, which pages arriving below the reader
  // would silently invalidate: 12% is twelve percent of one page before the
  // rest land and twelve percent of the whole work after. The section's height
  // is measured now, so the recorded fraction also survives a different font
  // size or column width than the one it was taken under — not hypothetical,
  // since both are reader settings.
  //
  // It waits for the chapter it is aiming at to have arrived, and gives up to
  // the top only once there is nothing left to deliver.
  useEffect(() => {
    if (!entire || !storyId || !entireChapters.length) return
    if (restoredRef.current === storyId) return
    let savedCh: number | null = null
    let frac = 0
    try {
      // The record the chapter reader and the story page already share, and its
      // `chapter` is where the reader last was IN ANY MODE — which is the point.
      // Someone who read chapters 1-12 one at a time, stopped mid-13, and then
      // opened the entire work finds chapter 13 rather than chapter 1.
      const prev = JSON.parse(localStorage.getItem("ficatlas:progress") ?? "{}")[storyId]
      if (prev && typeof prev.chapter === "number") {
        savedCh = prev.chapter
        frac = Number(prev.positions?.[prev.chapter] ?? prev.scrollPct) || 0
      }
    } catch {}
    if (savedCh != null && !sectionsRef.current.has(savedCh)) {
      // Not here yet — another page may still be carrying it. Try again when
      // that lands, rather than dropping the reader at the top of a work they
      // were a third of the way through.
      if (entireNext !== null) return
      savedCh = null      // the work is complete and it is not there: a hole
    }
    restoredRef.current = storyId
    if (savedCh == null) return

    const target = savedCh
    restoringRef.current = true
    let frame = 0
    let raf = 0
    const place = () => {
      if (!restoringRef.current) return
      const el = sectionsRef.current.get(target)
      if (el) {
        const r = el.getBoundingClientRect()
        // To the same reading line the fraction above was measured from, so a
        // restore lands the page exactly where the breadcrumb will name it.
        const top = window.scrollY + r.top + frac * r.height - READ_LINE
        if (Math.abs(window.scrollY - top) > 1) {
          window.scrollTo({ top, behavior: "instant" as ScrollBehavior })
        }
      }
      // Same ~500ms of re-asserting as the chapter placement above: images and
      // embedded media lay out after the text and grow the document under it,
      // and the browser's own back/forward restoration can land after ours.
      if (++frame < 30) raf = requestAnimationFrame(place)
      else restoringRef.current = false
    }
    raf = requestAnimationFrame(place)
    const yield_ = () => { restoringRef.current = false; frame = 30 }
    window.addEventListener("wheel", yield_, { passive: true, once: true })
    window.addEventListener("touchmove", yield_, { passive: true, once: true })
    window.addEventListener("keydown", yield_, { once: true })
    return () => {
      cancelAnimationFrame(raf)
      restoringRef.current = false
      window.removeEventListener("wheel", yield_)
      window.removeEventListener("touchmove", yield_)
      window.removeEventListener("keydown", yield_)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entire, storyId, entireChapters, entireNext])

  // Leaving a page from inside the reader. Every exit here used to be a <Link>,
  // which is why the ✕ sometimes did nothing — see lib/navigation.ts.
  const navigate = useCallback((href: string) => {
    setSettingsOpen(false)
    navigateTo(h => router.push(h), href, { networkAlive })
  }, [router, networkAlive])

  const goChapter = useCallback((n: number) => {
    navigate(`/story/${storyId}/chapter/${n}`)
  }, [navigate, storyId])

  // Used by the toolbar title, the floating ✕ and "All chapters" — all three are
  // the same action and all three must work when nothing else does.
  const exitReader = useCallback((e?: { preventDefault: () => void }) => {
    e?.preventDefault()
    navigate(`/story/${storyId}`)
  }, [navigate, storyId])

  // Prev/next come from the chapter numbers the story actually has, not from
  // `num ± 1` against a declared count. Stored chapter numbers are not
  // guaranteed contiguous (see downloadStoryForOffline), and an offline copy
  // holds only the chapters that downloaded — so counting was offering a Next
  // that led to a chapter nothing could load.
  //
  // Declared above the effects that depend on it: a dependency array is
  // evaluated during render, at the useEffect call, so listing a `const` from
  // further down the function body is a temporal-dead-zone error rather than a
  // late binding.
  // Neighbours are the nearest chapters we KNOW ABOUT, not `num ± 1`.
  //
  // Stored chapter numbers are not guaranteed contiguous, and offline the list
  // is whatever the save actually holds — so counting would offer a Next that
  // leads nowhere.
  //
  // The case this used to get wrong is the one that matters offline. indexOf
  // returns -1 when the chapter being READ is not in the list — you opened
  // chapter 5 online, went offline, and the saved copy holds 1-4, so `story`
  // comes back from IndexedDB with four chapters while `num` is 5. The old
  // fallback only handled an EMPTY list, so a non-empty list that simply did not
  // contain the current chapter left both prevNum and nextNum null and disabled
  // both buttons. Previous is the one you notice, because going back is what you
  // do when the chapter you are on will not load.
  //
  // Searching by value instead of by index gives the right answer in every case:
  // the nearest saved chapter below, and the nearest above.
  const numbers = (story?.chapters ?? []).map(c => c.number).sort((a, b) => a - b)
  const below = numbers.filter(n => n < num)
  const above = numbers.filter(n => n > num)
  const prevNum = below.length ? below[below.length - 1]
    // Nothing known at all: fall back to counting, which is better than a dead
    // button on a story whose chapter list never loaded.
    : (numbers.length === 0 && num > 1 ? num - 1 : null)
  const nextNum = above.length ? above[0]
    : (numbers.length === 0 && story && num < story.chapter_count ? num + 1 : null)

  // ── The same question for the entire-work view: neighbours of the chapter the
  // viewport is IN, since `num` is NaN there and there is no navigation to do.
  const atIdx = currentCh != null ? numbers.indexOf(currentCh) : -1
  const entirePrevCh = atIdx > 0 ? numbers[atIdx - 1] : null
  const entireNextCh = atIdx >= 0 && atIdx < numbers.length - 1 ? numbers[atIdx + 1] : null

  // Move to a chapter WITHIN the one page, by scrolling to its section.
  //
  // No `behavior` given on purpose: globals.css sets `html { scroll-behavior:
  // smooth }` and turns it off under `prefers-reduced-motion`, so this inherits
  // the reader's own motion setting instead of overriding it either way.
  const scrollToCh = useCallback((n: number | null | undefined) => {
    if (n == null) return
    const el = sectionsRef.current.get(n)
    if (!el) return
    const top = window.scrollY + el.getBoundingClientRect().top - READ_LINE
    window.scrollTo({ top })
    setCurrentCh(n)
    // Focus the heading so keyboard and screen-reader users arrive in the
    // chapter too, rather than being left on the button they pressed.
    const h = el.querySelector("h2")
    if (h instanceof HTMLElement) h.focus({ preventScroll: true })
  }, [])

  // Prefetch the neighbouring chapters into the router cache.
  //
  // This is what makes offline page-turning fast rather than merely possible.
  // A client-side transition needs the destination's RSC payload; without it
  // the router cannot move, navigateTo falls back to a document request, and
  // offline that means the service worker serves the shell and the entire app
  // boots again for every chapter. Prefetching while there IS a connection puts
  // next and previous in the router cache, so later — online or off — turning
  // the page is a local state change.
  //
  // Deliberately only the immediate neighbours: that is what the buttons and
  // the arrow keys reach, and prefetching further ahead would spend a reader's
  // connection on chapters they have not asked for.
  useEffect(() => {
    if (!storyId) return
    // Gated on a network that has DEMONSTRABLY answered, not on
    // `navigator.onLine` — which is `true` on a dead-but-associated connection,
    // so this guard never held in the case it existed for.
    //
    // It fired two router.prefetch() calls per chapter, and router.prefetch has
    // no abort and no timeout: two sockets per page turn, held until the
    // platform gave up minutes later, against the ~6 a browser allows per host.
    // That is what made reading saved chapters in sequence fail after the
    // first, with the text in IndexedDB the whole time. Measured: chapter 1 in
    // 0.3s, chapter 2 never.
    if (!networkAlive) return
    for (const n of [nextNum, prevNum]) {
      if (n != null) router.prefetch(`/story/${storyId}/chapter/${n}`)
    }

    // Warm the next chapter's TEXT as well as its route. Only forward, and only
    // once the current chapter has actually rendered — a reader who lands and
    // leaves immediately should not have paid for a chapter they never opened.
    if (nextNum == null || !chapter) return
    const key = cacheKey(storyId, nextNum)
    if (CHAPTER_CACHE.has(key)) return
    const ctl = new AbortController()
    // Bounded as well as abortable: the cleanup only runs on navigation, and a
    // reader who stays on one chapter would otherwise hold this socket open
    // indefinitely on a connection that is not answering.
    const give_up = setTimeout(() => ctl.abort(), 10_000)
    // requestIdleCallback keeps this off the critical path on a phone; the
    // setTimeout fallback is for Safari, which still does not implement it.
    const idle = (cb: () => void) =>
      typeof (window as any).requestIdleCallback === "function"
        ? (window as any).requestIdleCallback(cb, { timeout: 2000 })
        : window.setTimeout(cb, 400)
    const handle = idle(() => {
      // A prefetch nobody is waiting on, so it gets the background leash — and it
      // must give up rather than sit on a slot, because this fires on idle after
      // every chapter read and the reader's next tap needs that connection.
      fetchWithTimeout(`${API_BASE}/api/stories/${storyId}/chapters/${nextNum}`,
        { signal: ctl.signal }, BACKGROUND_TIMEOUT_MS)
        .then(r => r.ok ? r.json() : null)
        .then(c => { if (c) cachePut(key, c) })
        .catch(() => {})   // a failed prefetch must be silent — see CHAPTER_CACHE
    })
    return () => {
      ctl.abort()
      clearTimeout(give_up)
      if (typeof (window as any).cancelIdleCallback === "function") {
        (window as any).cancelIdleCallback(handle)
      } else {
        clearTimeout(handle as number)
      }
    }
  }, [storyId, nextNum, prevNum, router, chapter, networkAlive])

  // End-of-work recommendations: fetch similar stories when the reader reaches the
  // last chapter. Fired only once per story+chapter, and only when there IS a last
  // chapter — not on every page load for a 300-chapter work.
  useEffect(() => {
    if (!storyId || similarFor === storyId) return
    if (entire) {
      // The end of an entire work is when the LAST page has landed, not when
      // the first has: `nextNum` is null for this whole mode, so without this
      // the hook would offer "what next" while chapter one is still loading.
      if (entireNext !== null || !entireChapters.length) return
    } else if (nextNum != null) {
      return
    }
    setSimilarFor(storyId)
    setSimilar([])
    const ac = new AbortController()
    fetchWithTimeout(`${API_BASE}/api/stories/${storyId}/similar?count=6`,
      { signal: ac.signal }, BACKGROUND_TIMEOUT_MS)
      .then(r => r.ok ? r.json() : [])
      .then((data: SimilarCard[]) => setSimilar(data))
      .catch(() => {})
    return () => ac.abort()
  }, [storyId, nextNum, similarFor, entire, entireNext, entireChapters.length])

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
      // ← → turn the page: by navigating on a single-chapter read, by scrolling
      // to the adjacent section on an entire work. Same keys, same direction,
      // and the hint line at the foot promises both.
      if (e.key === "ArrowLeft") {
        if (prevNum != null) goChapter(prevNum)
        else if (entire) scrollToCh(entirePrevCh)
      }
      if (e.key === "ArrowRight") {
        if (nextNum != null) goChapter(nextNum)
        else if (entire) scrollToCh(entireNextCh)
      }
      if (e.key === "+" || e.key === "=") setFontSize(s => Math.min(s + 1, 24))
      if (e.key === "-") setFontSize(s => Math.max(s - 1, 13))
      if (e.key === "t") setTheme(t => t === "default" ? "sepia" : t === "sepia" ? "dark" : "default")
      if (e.key === "j") setJustify(j => !j)
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  // These are declared in the render body above; the handler only reads them
  // when it fires, by which point they are bound. All listed so the listener is
  // re-registered whenever the neighbours change — and `entirePrevCh`/
  // `entireNextCh` HAVE to be listed, because on a whole work they move on
  // every scroll across a heading while nothing else here would have re-armed
  // the listener. Omitting them left ← → walking a chapter out of date.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prevNum, nextNum, goChapter, entirePrevCh, entireNextCh, scrollToCh, entire])

  // The shell — toolbar, exit button, settings — renders in every state.
  //
  // It used to early-return a bare "Loading…" with no toolbar and no ✕ on it, so
  // for as long as a chapter took to arrive there was no way out of the reader
  // except the browser's own back button. When the backend was unreachable that
  // was not a moment, it was forever: the other half of "the ✕ doesn't always
  // exit" is that quite often there was no ✕ to press.
  const langCode = LANG_CODES[(story?.language || "English").trim()] || "en"
  const dir = RTL.has(langCode) ? "rtl" : undefined
  const minutes = Math.max(1, Math.round((chapter?.word_count || 0) / WORDS_PER_MINUTE))

  // `chapter_count_total` before `chapter_count`, and the distinction is the whole
// fix. Offline, `chapter_count` is how many chapters this device happens to
  // hold, so preferring it made the breadcrumb read "Chapter 3 of 4" on a
  // 199-chapter work — a figure that does not look like a bug in the breadcrumb,
  // it looks like the work is four chapters long. `chapter_count_total` is the
  // server's own count and is stored on every record.
  const totalLabel =
    story?.chapter_count_total || numbers.length || story?.chapter_count || "?"

  // ── Derived for the entire-work view ─────────────────────────────────────
  //
  // Derived here rather than inside the branches below, because the breadcrumb,
  // the ReadAloud control, the loading footer and the nav all need the same
  // three answers and computing them apart is how they come to disagree.
  const entireLoaded = entire && entireChapters.length > 0
  // Distinct from "loaded something": a work still paging in has both.
  const entireComplete = entireLoaded && entireNext === null
  const entireMinutes =
    Math.max(1, Math.round((story?.word_count || 0) / WORDS_PER_MINUTE))
  // How many chapters still lie beyond the last page the server sent. `numbers`
  // is what the work has; `entireNext` is how far it has got. Stale-by-a-page
  // at worst, and it is a status line rather than a control.
  const entireRemaining = typeof entireNext === "number"
    ? numbers.filter(n => n > entireNext).length
    : 0
  // How far the loaded text reaches, as one number: pages are fetched in
  // ascending order and appended, so everything at or below this has arrived.
  // Used to mark the chapters in the jump list that cannot be jumped to YET —
  // which is the whole point of the check, because picking one that has not
  // landed would otherwise scroll nowhere and say nothing.
  const arrivedThrough = entireChapters.length
    ? entireChapters[entireChapters.length - 1].number
    : 0
  // What ReadAloud reads: the whole work once the whole work is here, and the
  // FIRST CHAPTER ALONE until then. Concatenating what has arrived instead
  // would change `html` on every page that lands, and ReadAloud cancels
  // narration whenever its text changes — stopping it dead every second or two
  // while a long work finishes loading. One change, when loading finishes, and
  // nothing at all for the nine works in ten whose first page is also the last.
  const aloudHtml = !entireLoaded ? ""
    : entireComplete ? entireChapters.map(c => c.content).join("\n\n")
    : entireChapters[0]?.content ?? ""

  // End-of-work suggestions. Deliberately below the fold and visually
  // subordinate to the chapter nav — this reader is on the last page and the
  // last thing they clicked was "Next" going nowhere, so the hook is the
  // natural place to surface "what next". Uses the same /similar endpoint as
  // the story page, so the picks are the ones the algorithm already trusts.
  // Not shown to logged-out readers: the link targets go through /story/[id]
  // which requires an account for hosted chapters, and a click that 401s
  // erodes trust faster than it earns it.
  const endSuggestions = similar.length > 0 && (
    <div className="reader-end">
      <h4 className="reader-end__title">Still reading?</h4>
      <div className="reader-end__grid">
        {similar.slice(0, 6).map(s => (
          <Link key={s.id} href={`/story/${s.id}`} className="reader-end__card">
            <span className="reader-end__title-link">{s.title}</span>
            <span className="reader-end__author">by {s.author}</span>
            <span className="reader-end__meta">
              {s.word_count > 0 ? `${Math.round(s.word_count / 1000)}k words` : ""}
              {s.relationships?.[0] ? ` · ${s.relationships[0]}` : ""}
            </span>
          </Link>
        ))}
      </div>
    </div>
  )

  return (
    <main id="main" className="reader-shell" data-width={width} data-font={fontFamily}
      data-theme={theme === "site" ? siteTheme : theme}>
      {/* Keyboard users land on the toolbar first and would otherwise tab through
          every control before reaching a word of the story. */}
      <a href="#reader-body" className="skip-link">Skip to chapter text</a>
      <div className="reader-topbar reader-topbar--sticky">
        {/* Inside the toolbar, not fixed to the window — see .reader-progress in
            globals.css for why. */}
        <div className="reader-progress" style={{ width: `${scrollPct}%` }}
          role="progressbar" aria-label="Reading progress"
          aria-valuenow={Math.round(scrollPct)} aria-valuemin={0} aria-valuemax={100} />
        {/* title= carries the full text: the CSS clamps this to two lines, and
            a truncated title must still be readable on hover and to assistive
            tech. See the exception note on .reader-topbar .back-link. */}
        <a href={`/story/${storyId}`} className="back-link" onClick={exitReader}
          title={story?.title ? `Back to ${story.title}` : "Back to story"}>
          ← {story?.title ?? "Back to story"}
        </a>
        {/* Typography controls stay behind one "Aa" button. On a 390px phone
            the old inline controls wrapped to three rows before the first line
            of prose. The settings panel below is a bottom sheet on phones and
            a popover on desktop. */}
        <div className="reader-controls">
          {chapter && <ReadAloud key={`${storyId}/${chapter.number}`} html={chapter.content} language={langCode} />}
          {/* Keyed by the STORY, not by whatever chapter is under the viewport:
              a whole work is one continuous thing to listen to, and remounting
              per chapter would stop the narration dead at every heading. The
              mount stays put while pages append — only the text changes, once,
              when the whole work has arrived (see `aloudHtml`). */}
          {entireLoaded && (
            <ReadAloud key={`${storyId}/entire`} html={aloudHtml}
              language={langCode} scope="work" />
          )}
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
                {(["site", "default", "sepia", "dark"] as const).map(t => (
                  <button key={t} onClick={() => setTheme(t)}
                    className={theme === t ? "is-on" : ""}
                    aria-pressed={theme === t}
                    title={t === "site" ? "Follow the site theme" : undefined}>
                    {t === "site" ? "Site" : t === "default" ? "Light"
                      : t === "sepia" ? "Sepia" : "Dark"}
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

      {/* Floating exit button — exit the reader from anywhere without scrolling
          up. Hidden while the settings sheet is open: the sheet's backdrop sits
          above it, so it was still painted but no longer clickable, and on a
          phone the bottom sheet covers it outright. A button that is visible and
          does nothing is the worst of the three options — the sheet has its own
          ✕, its backdrop closes it, and so does Escape. */}
      {!settingsOpen && (
        <a href={`/story/${storyId}`} className="reader-fab" onClick={exitReader}
          title="Back to story page" aria-label="Exit reader">✕</a>
      )}

      {/* fontSize is expressed in rem, not px. A px size ignores the reader's
          own browser font-size setting entirely, which is the setting people
          with low vision actually rely on; rem scales with it and still honours
          the in-app A+/A- buttons on top. */}
      {!chapter && !entireLoaded && !loadError && (
        <p className="loading" role="status" aria-live="polite">
          {entire ? "Loading the work…" : "Loading…"}
        </p>
      )}

      {/* An honest failure, in the vocabulary the rest of the site uses. This
          screen said "Unavailable offline" for every possible failure, including
          a 404 on a chapter that was never there and a 500 from our own server —
          sending people to check a connection that was fine. */}
      {!chapter && loadError && (
        <div className="reader-error" role="alert">
          <h1 className="reader-error__title">
            {loadError.kind === "offline"
              ? "Not saved on this device"
              : entire ? "Couldn't load this work" : "Couldn't load this chapter"}
          </h1>
          <p className="reader-error__body">
            {loadError.kind === "offline"
              ? // A work that had ANY of it saved would be showing it, because
                // the local copy is read before the network is asked — so this
                // screen offline means none of it is here, and "this chapter
                // isn't among the N saved" would be answering a question that
                // was not asked.
                (entire
                  ? "You're offline and this work isn't saved on this device. Open it while online, then tap “Save offline” on the story page to read the whole thing later."
                  : numbers.length
                    ? `You're offline and this chapter isn't among the ${numbers.length} saved on this device.`
                    : "You're offline and this story isn't saved here. Open it while online, then tap “Save offline” on the story page to read it later.")
              : loadError.message}
          </p>
          {/* A way out, using what the device actually holds.
              Without this the screen was a dead end: on a partly saved work you
              were told the chapter is missing and given nothing but "Back",
              while six other chapters sat in IndexedDB one tap away. The nearest
              saved chapter on either side is what someone wants here — usually
              the one before, since arriving at a gap means carrying on is what
              just failed. */}
          {(prevNum != null || nextNum != null) && (
            <div className="reader-error__actions">
              {prevNum != null && (
                <button className="btn btn--primary"
                  onClick={() => goChapter(prevNum)}>
                  ← Chapter {prevNum}{numbers.length ? " (saved)" : ""}
                </button>
              )}
              {nextNum != null && (
                <button className="btn btn--ghost"
                  onClick={() => goChapter(nextNum)}>
                  Chapter {nextNum} →
                </button>
              )}
            </div>
          )}
          <div className="reader-error__actions">
            {/* The door the message is already pointing at.
                Reading hosted text needs an account on the public site
                (REQUIRE_LOGIN_TO_READ — see api/stories.py for why), so a
                logged-out reader clicking "Read Chapter 1" lands here and is
                told "you are not signed in… signing in again may fix it" — with
                nothing to sign in WITH. The message asked for an action and
                withheld the control for it, which is the one thing an error
                screen must not do.
                Only when there is nobody signed in: a 403 for somebody who IS
                signed in means the work is not theirs to read, and offering
                them a login would be telling them to try the same key again. */}
            {loadError.kind === "denied" && !user && (
              <a className="btn btn--primary"
                href={`/login?next=${encodeURIComponent(
                  entire ? `/story/${storyId}/entire` : `/story/${storyId}/chapter/${num}`)}`}>
                Sign in to read
              </a>
            )}
            {loadError.retryable && (
              <button className="btn btn--primary" onClick={() => setReloadKey(k => k + 1)}>
                Try again
              </button>
            )}
            <a href={`/story/${storyId}`} className="btn btn--ghost" onClick={exitReader}>
              Back to story
            </a>
          </div>
        </div>
      )}

      {chapter && (
        <article className="reader" data-width={width} data-font={fontFamily}
          data-justify={justify ? "on" : "off"}
          lang={langCode} dir={dir}
          style={{ fontSize: `${(fontSize / 16).toFixed(3)}rem`, lineHeight }}>
          <header className="reader__header">
            <p className="reader__breadcrumb">
              Chapter {num} of {totalLabel}
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
      )}

      {/* The same reader shell over the whole work rather than one chapter —
          same width, size, theme, justification and language handling, because
          a reader who set those on a phone should not get a second, different
          typography for the same story told in the other direction. */}
      {entireLoaded && (
        <article className="reader" id="reader-body" data-width={width}
          data-font={fontFamily} data-justify={justify ? "on" : "off"}
          lang={langCode} dir={dir}
          style={{ fontSize: `${(fontSize / 16).toFixed(3)}rem`, lineHeight }}>
          <header className="reader__header">
            <p className="reader__breadcrumb">
              Entire work
              <span className="dot"> · </span>
              {currentCh != null
                ? `Chapter ${currentCh} of ${totalLabel}`
                // Before the first heading, or above the title block: say how
                // much has ARRIVED rather than leaving a blank. The count is
                // expected to move — it is the loading state, not an error.
                : `${entireChapters.length} of ${totalLabel} chapters`}
              <span className="dot"> · </span>
              <span>{entireMinutes} min read</span>
            </p>
            <h1 className="reader__title" tabIndex={-1} ref={headingRef}>
              {story?.title}
            </h1>
            {/* Named, because an entire work is read as a document in its own
                right rather than continued from a page that had already said
                whose it is. */}
            {story?.author && <p className="reader__byline">by {story.author}</p>}
            {story?.summary && <p className="reader__summary">{story.summary}</p>}
          </header>

          {entireChapters.map(ch => (
            <ReaderPart key={ch.number} ch={ch} totalLabel={totalLabel}
              onRef={setSectionRef} />
          ))}

          {/* Two honest states instead of one spinner: the work still coming
              says how much is left, and the page that will not arrive says so
              and offers it again — neither of which takes the several hundred
              kilobytes already on screen away from the person reading them. */}
          {entireStalled ? (
            <p className="reader-more" role="status">
              {entireRemaining > 0 &&
                `${entireRemaining} more ${entireRemaining === 1 ? "chapter" : "chapters"} never arrived. `}
              <button className="btn btn--ghost"
                onClick={() => { setEntireStalled(false); setEntireRetry(r => r + 1) }}>
                Load the rest
              </button>
            </p>
          ) : typeof entireNext === "number" && (
            <p className="reader-more" role="status" aria-live="polite">
              {entireRemaining > 0
                ? `Loading ${entireRemaining} more ${entireRemaining === 1 ? "chapter" : "chapters"}…`
                : "Loading the rest…"}
            </p>
          )}
        </article>
      )}

      {chapter && (
        <>
          <nav className="reader-nav">
            <button className="reader-nav__btn" disabled={prevNum == null}
              onClick={() => prevNum != null && goChapter(prevNum)}>← Previous</button>
            <div className="reader-nav__mid">
              {/* Somewhere to go that is not one chapter away.
                  Until this existed the reader had three controls — Previous,
                  All chapters, Next — and on a 199-chapter work that is the
                  only way through: 198 clicks, or back to the story page to
                  scroll a list of 200 links and find your place in it. Neither
                  is a way to reach chapter 140, and neither is a way to reach
                  the LATEST chapter of something still being written, which is
                  what a reader coming back to a WIP wants.
                  A native <select> rather than a custom menu: the browser
                  gives keyboard typeahead, a scrollable list that knows how
                  tall the screen is, and a real picker on a phone — all of
                  which would have to be rebuilt, worse, by hand. It lists the
                  chapters the reader actually HAS, which offline is the ones
                  that downloaded, for the same reason prevNum and nextNum are
                  computed from stored numbers rather than from num ± 1. */}
              {numbers.length > 1 && (
                <select className="reader-nav__jump" value={num}
                  aria-label="Jump to a chapter"
                  onChange={e => goChapter(Number(e.target.value))}>
                  {(story?.chapters ?? []).map(c => (
                    <option key={c.number} value={c.number}>
                      {c.title ? `${c.number}. ${c.title}` : `Chapter ${c.number}`}
                    </option>
                  ))}
                </select>
              )}
              {/* The other way through a multi-chapter work — AO3's "Entire
                  Work", the whole thing on one continuous page. Offered only
                  when there is more than one chapter to join up: 21,310 of the
                  29,785 hosted works are a single chapter, and on those this
                  button would promise a mode identical to the screen it is
                  already on. */}
              {numbers.length > 1 && (
                <a href={`/story/${storyId}/entire`} className="reader-nav__index"
                  onClick={e => { e.preventDefault(); navigate(`/story/${storyId}/entire`) }}>
                  Entire work
                </a>
              )}
              <a href={`/story/${storyId}`} className="reader-nav__index"
                onClick={exitReader}>All chapters</a>
            </div>
            <button className="reader-nav__btn" disabled={nextNum == null}
              onClick={() => nextNum != null && goChapter(nextNum)}>Next →</button>
            {story && story.chapters && story.chapters.length > 0 && num != null
              && story.chapters[story.chapters.length - 1].number > num && (() => {
              const lastNum = story.chapters[story.chapters.length - 1].number
              return (
                <button
                  className="reader-nav__skip"
                  title="Skip to the latest chapter (this is a work in progress)"
                  onClick={() => goChapter(lastNum)}>
                  ↥ Latest
                </button>
              )
            })()}
          </nav>

          <p className="reader-hint">Keys: ← → chapters · + − text size · t theme · j justify. Line spacing, serif/sans &amp; column width: buttons above.</p>

          {nextNum == null && endSuggestions}
        </>
      )}

      {/* The entire-work twin of the block above. Previous/Next still turn to
          the adjacent chapter — they do it by scrolling instead of by
          navigation, so the muscle memory, the hint line and the ← → keys all
          carry over unchanged between the two ways of reading. */}
      {entireLoaded && (
        <>
          <nav className="reader-nav">
            <button className="reader-nav__btn" disabled={entirePrevCh == null}
              onClick={() => scrollToCh(entirePrevCh)}>← Previous</button>
            <div className="reader-nav__mid">
              {/* The same native select as the chapter nav, for the same
                  reasons: keyboard typeahead, a picker that knows how tall the
                  screen is, no custom menu to rebuild badly. Here it SCROLLS
                  rather than navigates, and it lists the work's whole outline —
                  with the chapters whose text has not arrived yet marked and
                  disabled, so the list never offers a jump that lands
                  nowhere. */}
              {numbers.length > 1 && (
                <select className="reader-nav__jump"
                  value={currentCh ?? numbers[0] ?? ""}
                  aria-label="Jump to a chapter"
                  onChange={e => scrollToCh(Number(e.target.value))}>
                  {(story?.chapters ?? []).map(c => {
                    const pending = c.number > arrivedThrough
                    return (
                      <option key={c.number} value={c.number} disabled={pending}>
                        {c.title ? `${c.number}. ${c.title}` : `Chapter ${c.number}`}
                        {pending ? " — loading" : ""}
                      </option>
                    )
                  })}
                </select>
              )}
              {/* Out of the continuous view and back into paging, at the chapter
                  the reader is standing in rather than at chapter 1. */}
              <a href={`/story/${storyId}/chapter/${currentCh ?? numbers[0] ?? 1}`}
                className="reader-nav__index"
                onClick={e => {
                  e.preventDefault()
                  navigate(`/story/${storyId}/chapter/${currentCh ?? numbers[0] ?? 1}`)
                }}>
                Chapter by chapter
              </a>
            </div>
            <button className="reader-nav__btn" disabled={entireNextCh == null}
              onClick={() => scrollToCh(entireNextCh)}>Next →</button>
          </nav>

          <p className="reader-hint">Keys: ← → chapter · + − text size · t theme · j justify. Line spacing, serif/sans &amp; column width: buttons above.</p>

          {endSuggestions}
        </>
      )}
</main>
  )
}
