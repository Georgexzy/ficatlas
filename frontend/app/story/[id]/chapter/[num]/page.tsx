"use client"
import { useCallback, useEffect, useRef, useState } from "react"
import { useParams, useRouter } from "next/navigation"
import { describeError, type Failure } from "@/lib/errors"
import { navigateTo } from "@/lib/navigation"

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

export default function ChapterPage() {
  const params = useParams()
  const router = useRouter()
  const storyId = params?.id as string
  const num = Number(params?.num)
  const [chapter, setChapter] = useState<ChapterFull | null>(null)
  const [story, setStory] = useState<StoryMin | null>(null)
  const [loadError, setLoadError] = useState<Failure | null>(null)
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
  useEffect(() => {
    if (!storyId || !num) return
    let cancelled = false
    const ctl = new AbortController()
    // Long enough for a genuinely slow search-index box, short enough that the
    // saved copy is offered while the reader is still willing to wait for it.
    const timer = setTimeout(() => ctl.abort(), 20_000)

    setChapter(null)
    setLoadError(null)

    const fromOffline = async (): Promise<boolean> => {
      const { getOfflineStory } = await import("@/lib/offline")
      const saved = await getOfflineStory(storyId)
      if (!saved || cancelled) return false
      const ch = saved.chapters.find(c => c.number === Number(num))
      // The story is set even when THIS chapter is not among the saved ones.
      //
      // It used to return early, leaving `story` null, and a null story means no
      // chapter list — so the error screen had no idea which chapters the device
      // actually holds and offered no way to reach them. Offline, on a partly
      // saved work, that is a dead end: "not saved on this device" with no route
      // back to the six chapters sitting in IndexedDB.
      setStory({
        id: saved.id, title: saved.title, author: saved.author,
        // What is actually held, and the numbers held — see the note on
        // prevNum/nextNum for why the numbers matter offline.
        chapter_count: saved.chapters.length,
        chapters: saved.chapters.map(c => ({ number: c.number, title: c.title })),
      } as any)
      if (!ch) return false        // story known, this chapter not held
      setChapter({
        number: ch.number, title: ch.title, content: ch.content,
        start_note: ch.start_note, end_note: ch.end_note, summary: ch.summary,
      } as any)
      return true
    }

    const load = async () => {
      // Offline, go to the saved copy first instead of racing two fetches that
      // cannot succeed. Both were doomed, the failures had to unwind before the
      // fallback even started, and on a flaky connection — the case where
      // navigator.onLine is most likely to be right — they could sit unresolved
      // rather than failing fast. Reading IndexedDB directly is immediate.
      //
      // Only trusted in the false direction (see lib/errors.ts): if it claims
      // to be online we still try the network, because it is often wrong about
      // that. If it says offline and we hold nothing, the code below falls
      // through to the network attempt anyway rather than declaring failure on
      // the strength of a flag.
      if (!navigator.onLine && await fromOffline()) return
      if (cancelled) return

      try {
        // A chapter warmed by the prefetch below is already in memory, so the
        // usual next-chapter tap renders without a round trip or a spinner.
        const warm = CHAPTER_CACHE.get(cacheKey(storyId, num))
        const [s, c] = await Promise.all([
          fetch(`${API_BASE}/api/stories/${storyId}`, { signal: ctl.signal })
            .then(r => { if (!r.ok) throw describeError(null, r.status); return r.json() }),
          warm ?? fetch(`${API_BASE}/api/stories/${storyId}/chapters/${num}`, { signal: ctl.signal })
            .then(r => { if (!r.ok) throw describeError(null, r.status); return r.json() }),
        ])
        if (cancelled) return
        setStory(s)
        setChapter(c)
      } catch (e: any) {
        if (cancelled) return
        // Prefer the saved copy over any error message, whatever the cause: if we
        // hold the text, showing it beats explaining why the server did not.
        if (await fromOffline()) return
        if (cancelled) return
        setLoadError(e?.kind ? e : describeError(e))
      } finally {
        clearTimeout(timer)
      }
    }
    load()

    return () => { cancelled = true; clearTimeout(timer); ctl.abort() }
  }, [storyId, num, reloadKey])

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
  const writeProgress = useCallback((pct: number | null) => {
    if (!story || !chapter) return
    try {
      const all = JSON.parse(localStorage.getItem("ficatlas:progress") ?? "{}")
      const prev = all[story.id] || {}
      const positions = { ...(prev.positions || {}) }
      if (pct != null) positions[chapter.number] = Math.min(1, Math.max(0, pct))
      all[story.id] = {
        ...prev,
        chapter: chapter.number,
        positions,
        scrollPct: positions[chapter.number] ?? 0,
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
    const flush = () => { if (timer) { clearTimeout(timer); timer = null } save() }
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

  // Leaving a page from inside the reader. Every exit here used to be a <Link>,
  // which is why the ✕ sometimes did nothing — see lib/navigation.ts.
  const navigate = useCallback((href: string) => {
    setSettingsOpen(false)
    navigateTo(h => router.push(h), href)
  }, [router])

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
    if (!navigator.onLine) return          // nothing to warm a cache from
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
    // requestIdleCallback keeps this off the critical path on a phone; the
    // setTimeout fallback is for Safari, which still does not implement it.
    const idle = (cb: () => void) =>
      typeof (window as any).requestIdleCallback === "function"
        ? (window as any).requestIdleCallback(cb, { timeout: 2000 })
        : window.setTimeout(cb, 400)
    const handle = idle(() => {
      fetch(`${API_BASE}/api/stories/${storyId}/chapters/${nextNum}`, { signal: ctl.signal })
        .then(r => r.ok ? r.json() : null)
        .then(c => { if (c) cachePut(key, c) })
        .catch(() => {})   // a failed prefetch must be silent — see CHAPTER_CACHE
    })
    return () => {
      ctl.abort()
      if (typeof (window as any).cancelIdleCallback === "function") {
        (window as any).cancelIdleCallback(handle)
      } else {
        clearTimeout(handle as number)
      }
    }
  }, [storyId, nextNum, prevNum, router, chapter])

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
      if (e.key === "ArrowLeft" && prevNum != null) goChapter(prevNum)
      if (e.key === "ArrowRight" && nextNum != null) goChapter(nextNum)
      if (e.key === "+" || e.key === "=") setFontSize(s => Math.min(s + 1, 24))
      if (e.key === "-") setFontSize(s => Math.max(s - 1, 13))
      if (e.key === "t") setTheme(t => t === "default" ? "sepia" : t === "sepia" ? "dark" : "default")
      if (e.key === "j") setJustify(j => !j)
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  // prevNum/nextNum are declared below, in the render body; the handler only
  // reads them when it fires, by which point they are bound. They are listed so
  // the listener is re-registered whenever the chapter's neighbours change.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prevNum, nextNum, goChapter])

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

  const totalLabel = numbers.length || story?.chapter_count || "?"

  return (
    <div className="reader-shell" data-width={width} data-font={fontFamily}
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
      {!chapter && !loadError && (
        <p className="loading" role="status" aria-live="polite">Loading…</p>
      )}

      {/* An honest failure, in the vocabulary the rest of the site uses. This
          screen said "Unavailable offline" for every possible failure, including
          a 404 on a chapter that was never there and a 500 from our own server —
          sending people to check a connection that was fine. */}
      {!chapter && loadError && (
        <div className="reader-error" role="alert">
          <h1 className="reader-error__title">
            {loadError.kind === "offline" ? "Not saved on this device" : "Couldn't load this chapter"}
          </h1>
          <p className="reader-error__body">
            {loadError.kind === "offline"
              ? (numbers.length
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

      {chapter && (
        <>
          <nav className="reader-nav">
            <button className="reader-nav__btn" disabled={prevNum == null}
              onClick={() => prevNum != null && goChapter(prevNum)}>← Previous</button>
            <a href={`/story/${storyId}`} className="reader-nav__index"
              onClick={exitReader}>All chapters</a>
            <button className="reader-nav__btn" disabled={nextNum == null}
              onClick={() => nextNum != null && goChapter(nextNum)}>Next →</button>
          </nav>

          <p className="reader-hint">Keys: ← → chapters · + − text size · t theme · j justify. Line spacing, serif/sans &amp; column width: buttons above.</p>
        </>
      )}
    </div>
  )
}
