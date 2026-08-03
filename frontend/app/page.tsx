"use client"
export const dynamic = "force-dynamic"
import { useState, useCallback, useTransition, useEffect, useRef, Suspense } from "react"
import { useRouter, useSearchParams, usePathname } from "next/navigation"
import Link from "next/link"
import OfflineLink from "./OfflineLink"
import HelpTip from "./HelpTip"
import type { SearchParams, SearchResponse, StoryCard } from "@/lib/types"
import { searchStories, formatWordCount, formatNumber, chapterDisplay,
         SITE_LABELS, RATING_LABELS, SORT_OPTIONS, WORD_COUNT_PRESETS,
         DATE_PRESETS, AO3_WARNINGS, CATEGORIES } from "@/lib/api"
import { parseQuery, parsedToSearchParams, type ParsedToken } from "@/lib/queryParser"
import { storyLink, isSeedUrl } from "@/lib/storyLinks"
import SyntaxHelp from "./SyntaxHelp"
import SiteHeader from "./SiteHeader"
import { useAuth } from "@/lib/auth"

// ── Helpers ───────────────────────────────────────────────────────────────────
function csv(s?: string): string[] {
  return s ? s.split(",").map(x => x.trim()).filter(Boolean) : []
}
function joinCsv(arr: string[]): string | undefined {
  return arr.length ? arr.join(",") : undefined
}
function detectFicUrl(s: string): { site: string; url: string } | null {
  const t = s.trim()
  if (/^https?:\/\/(www\.)?archiveofourown\.org\/works\/\d+/.test(t)) return { site: "ao3", url: t }
  if (/^https?:\/\/(www\.|m\.)?fanfiction\.net\/s\/\d+/.test(t)) return { site: "ffnet", url: t }
  return null
}

const SITE_OPTIONS = [
  { id: "ao3",          label: "AO3" },
  { id: "ffnet",        label: "FF.net" },
  { id: "fictionalley", label: "FicAlley" },
]
const RATING_OPTIONS = [
  { id: "G",  label: "General" },
  { id: "T",  label: "Teen" },
  { id: "M",  label: "Mature" },
  { id: "E",  label: "Explicit" },
  { id: "NR", label: "Not Rated" },
]


// ── Expandable summary ────────────────────────────────────────────────────────
// Summaries were clamped to three lines with no way to read the rest, so a
// listing showed a sentence that stopped mid-thought. This adds an expander —
// but only when the text is ACTUALLY being cut off, measured after layout.
// Offering "more" on a two-line summary that already fits is just noise.
function Summary({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false)
  const [overflows, setOverflows] = useState(false)
  const ref = useRef<HTMLParagraphElement | null>(null)

  useEffect(() => {
    const measure = () => {
      const el = ref.current
      if (!el) return
      // Compare against the clamped height, so this must run while collapsed.
      if (!expanded) setOverflows(el.scrollHeight > el.clientHeight + 1)
    }
    measure()
    // Rotating a phone or resizing changes how many lines fit.
    window.addEventListener("resize", measure)
    return () => window.removeEventListener("resize", measure)
  }, [text, expanded])

  return (
    <div className="card__summary-wrap">
      <p ref={ref} className={`card__summary ${expanded ? "card__summary--open" : ""}`}>
        {text}
      </p>
      {(overflows || expanded) && (
        <button
          className="card__summary-toggle"
          aria-expanded={expanded}
          onClick={e => { e.preventDefault(); e.stopPropagation(); setExpanded(v => !v) }}
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  )
}

// ── Tag list with expand/collapse ─────────────────────────────────────────────
function TagList({ tags, className, kind = "tags" }: {
  tags: string[]; className?: string; kind?: "tags" | "fandoms" | "relationships" | "characters"
}) {
  const [expanded, setExpanded] = useState(false)
  const shown = expanded ? tags : tags.slice(0, 5)
  const extra = tags.length - 5
  return (
    <div className={`tag-list ${className ?? ""}`}>
      {shown.map(t => (
        <Link key={t} href={`/?${kind}=${encodeURIComponent(t)}`} className="tag tag--clickable">{t}</Link>
      ))}
      {!expanded && extra > 0 && <button onClick={() => setExpanded(true)} className="tag tag--more">+{extra}</button>}
      {expanded  && extra > 0 && <button onClick={() => setExpanded(false)} className="tag tag--more">less</button>}
    </div>
  )
}

// ── Collapsible filter section ────────────────────────────────────────────────
function FilterSection({ label, children, defaultOpen = false, highlighted = false, count = 0 }: {
  label: string; children: React.ReactNode; defaultOpen?: boolean; highlighted?: boolean; count?: number
}) {
  const [open, setOpen] = useState(defaultOpen || highlighted)
  useEffect(() => { if (highlighted) setOpen(true) }, [highlighted])
  return (
    <div className={`filter-section ${highlighted ? "filter-section--lit" : ""}`}>
      <button className="filter-section__toggle" onClick={() => setOpen(o => !o)}>
        <span className="filter-section__label">
          {label}
          {count > 0 && <span className="filter-section__count">{count}</span>}
          {highlighted && <span className="filter-section__dot" />}
        </span>
        <span className={`filter-section__chevron ${open ? "open" : ""}`}>▸</span>
      </button>
      {open && <div className="filter-section__body">{children}</div>}
    </div>
  )
}

// ── Tag input (add/remove chips) ──────────────────────────────────────────────
function TagInput({ value, onChange, placeholder, highlighted = [], kind }: {
  value: string[]; onChange: (v: string[]) => void; placeholder?: string
  highlighted?: string[]; kind?: "fandom" | "relationship" | "character" | "tag"
}) {
  const [input, setInput] = useState("")
  const [suggestions, setSuggestions] = useState<{ value: string; count: number }[]>([])
  const [showSug, setShowSug] = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const boxRef = useRef<HTMLDivElement | null>(null)

  const add = (val?: string) => {
    const v = (val ?? input).trim()
    if (v && !value.includes(v)) onChange([...value, v])
    setInput(""); setSuggestions([]); setShowSug(false); setActiveIdx(-1)
  }

  // Debounced autocomplete fetch
  useEffect(() => {
    if (!kind) return
    const q = input.trim()
    if (q.length < 1) { setSuggestions([]); return }
    const t = setTimeout(async () => {
      try {
        const r = await fetch(`/api/stats/suggest?kind=${kind}&q=${encodeURIComponent(q)}&limit=8`)
        if (r.ok) {
          const data = await r.json()
          setSuggestions(data); setShowSug(true); setActiveIdx(-1)
        }
      } catch {}
    }, 200)
    return () => clearTimeout(t)
  }, [input, kind])

  // Close suggestions on outside click
  useEffect(() => {
    if (!showSug) return
    const close = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setShowSug(false)
    }
    document.addEventListener("click", close)
    return () => document.removeEventListener("click", close)
  }, [showSug])

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (showSug && suggestions.length > 0) {
      if (e.key === "ArrowDown") { e.preventDefault(); setActiveIdx(i => Math.min(i + 1, suggestions.length - 1)); return }
      if (e.key === "ArrowUp")   { e.preventDefault(); setActiveIdx(i => Math.max(i - 1, -1)); return }
      if (e.key === "Enter" && activeIdx >= 0) { e.preventDefault(); add(suggestions[activeIdx].value); return }
      if (e.key === "Escape") { setShowSug(false); return }
    }
    if (e.key === "Enter") { e.preventDefault(); add() }
  }

  return (
    <div className="tag-input" ref={boxRef}>
      <div className="tag-input__row">
        <input value={input}
          onChange={e => setInput(e.target.value)}
          onFocus={() => { if (suggestions.length) setShowSug(true) }}
          onKeyDown={onKeyDown}
          placeholder={placeholder} className="tag-input__field" />
        <button onClick={() => add()} className="tag-input__add" aria-label="Add">+</button>
      </div>
      {showSug && suggestions.length > 0 && (
        <ul className="tag-suggest">
          {suggestions.map((s, i) => (
            <li key={s.value}
              className={`tag-suggest__item ${i === activeIdx ? "tag-suggest__item--active" : ""}`}
              onMouseDown={e => { e.preventDefault(); add(s.value) }}
              onMouseEnter={() => setActiveIdx(i)}>
              <span className="tag-suggest__value">{s.value}</span>
              <span className="tag-suggest__count">{s.count.toLocaleString()}</span>
            </li>
          ))}
        </ul>
      )}
      {value.length > 0 && (
        <div className="tag-input__chips">
          {value.map(v => (
            <button key={v} onClick={() => onChange(value.filter(x => x !== v))}
              className={`chip ${highlighted.includes(v) ? "chip--lit" : ""}`}
              title="Remove">
              {highlighted.includes(v) && "⌕ "}{v} ✕
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Pill group ────────────────────────────────────────────────────────────────
function Pills({ options, selected, onToggle, highlighted = [] }: {
  options: { id: string; label: string }[]
  selected: string[]; onToggle: (id: string) => void; highlighted?: string[]
}) {
  return (
    <div className="pills">
      {options.map(o => (
        <button key={o.id} onClick={() => onToggle(o.id)}
          className={`pill ${selected.includes(o.id) ? "pill--on" : ""} ${highlighted.includes(o.id) ? "pill--lit" : ""}`}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

// ── Token strip (parsed operators below search bar) ───────────────────────────
function TokenStrip({ tokens, onRemove }: { tokens: ParsedToken[]; onRemove: (raw: string) => void }) {
  if (!tokens.length) return null
  const KEY_SHORT: Record<string, string> = {
    fandoms: "fandom", relationships: "ship", characters: "char",
    tags: "tag", ratings: "rating", status: "status",
    word_count: "words", updated_after: "since", language: "lang",
    sites: "site", crossovers: "xover", warnings: "warn", categories: "cat",
  }
  return (
    <div className="token-strip">
      {tokens.map((t, i) => (
        <button key={i} className={`token ${t.exclude ? "token--exc" : ""}`}
          onClick={() => onRemove(t.raw)} title="Remove filter">
          <span className="token__key">{t.exclude ? "−" : ""}{KEY_SHORT[t.key] ?? t.key}:</span>
          <span className="token__val">{t.value}</span>
          <span className="token__x">✕</span>
        </button>
      ))}
    </div>
  )
}

// ── Result card ───────────────────────────────────────────────────────────────
function StoryCard({ story }: { story: StoryCard }) {
  const [bookmarked, setBookmarked] = useState(false)
  const [importing, setImporting] = useState(false)
  const [importedId, setImportedId] = useState<string | null>(null)

  useEffect(() => {
    try {
      const list = JSON.parse(localStorage.getItem("ficatlas:bookmarks") ?? "[]")
      setBookmarked(list.some((b: any) => b.id === story.id))
    } catch {}
  }, [story.id])

  const toggleBookmark = (e: React.MouseEvent) => {
    e.preventDefault(); e.stopPropagation()
    try {
      const list = JSON.parse(localStorage.getItem("ficatlas:bookmarks") ?? "[]")
      if (bookmarked) {
        localStorage.setItem("ficatlas:bookmarks",
          JSON.stringify(list.filter((b: any) => b.id !== story.id)))
        setBookmarked(false)
      } else {
        list.unshift({ id: story.id, title: story.title, author: story.author,
                       site: story.site, url: story.url, savedAt: new Date().toISOString() })
        localStorage.setItem("ficatlas:bookmarks", JSON.stringify(list.slice(0, 200)))
        setBookmarked(true)
      }
    } catch {}
  }

  const importToRead = async (e: React.MouseEvent) => {
    e.preventDefault(); e.stopPropagation()
    if (importing) return
    setImporting(true)
    try {
      const fd = new FormData(); fd.append("url", story.url)
      const r = await fetch(`/api/library/import-url`, { method: "POST", body: fd })
      const data = await r.json()
      if (data.id) {
        setImportedId(data.id)
        // Auto-redirect to the reader after a brief success state
        setTimeout(() => { window.location.href = `/story/${data.id}/chapter/1` }, 350)
      } else {
        alert(`Import failed: ${data.error || data.detail || "unknown"}`)
      }
    } catch (err: any) {
      alert(`Import failed: ${err.message || err}`)
    } finally {
      setImporting(false)
    }
  }

  const { href: externalUrl, label: externalLabel } = storyLink(story, SITE_LABELS)

  // Can we one-click import? Only sites FicHub handles, not already hosted, and
  // not a metadata-only seed row — there is no real page for FicHub to fetch.
  const canImport = !story.is_hosted && !isSeedUrl(story.url)
    && (story.site === "ao3" || story.site === "ffnet")

  return (
    <article className={`card ${story.is_live ? "card--live" : ""}`}>
      <div className="card__top">
        <div className="card__title-row">
          <Link href={`/story/${story.id}`} className="card__title">{story.title}</Link>
          <div className="card__badges">
            <span className={`badge badge--site-${story.site}`}>{SITE_LABELS[story.site] ?? story.site}</span>
            {story.cross_post_urls && story.cross_post_urls.length > 0 && (
              <span className="badge badge--crosspost"
                title={`Also posted on:\n${story.cross_post_urls.join("\n")}`}>
                +{story.cross_post_urls.length} {story.cross_post_urls.length === 1 ? "copy" : "copies"}
              </span>
            )}
            {story.rating && <span className={`badge badge--rating badge--${story.rating.toLowerCase()}`}>{RATING_LABELS[story.rating] ?? story.rating}</span>}
            {story.status === "complete" && <span className="badge badge--complete">Complete</span>}
            {story.tags?.includes("dlp_library") && <span className="badge badge--dlp" title="Curated by DarkLordPotter">DLP</span>}
            {story.is_live && <span className="badge badge--live">Live</span>}
          </div>
        </div>
        <p className="card__byline">
          {/* Clicking the author browses everything they wrote, ACROSS archives —
              an AO3 or FF.net user page only ever shows what they posted there. */}
          <Link href={`/?author=${encodeURIComponent(story.author)}`}
            className="card__author-link" title={`All works by ${story.author}`}>
            {story.author}
          </Link>
          {story.author_url && (
            <a href={story.author_url} target="_blank" rel="noopener noreferrer"
              className="card__author-ext" title="Author's page on the original site">↗</a>
          )}
          {story.fandoms.length > 0 && (
            <> · <span className="card__fandom">
              {story.fandoms.slice(0, 2).map((f, i) => (
                <span key={f}>
                  {i > 0 && ", "}
                  <Link href={`/?fandoms=${encodeURIComponent(f)}`} className="card__fandom-link">{f}</Link>
                </span>
              ))}
              {story.fandoms.length > 2 ? ` +${story.fandoms.length - 2}` : ""}
            </span></>
          )}
        </p>
      </div>

      {story.summary && <Summary text={story.summary} />}

      <div className="card__meta">
        {story.word_count > 0
          ? <span title="Words">📄 {formatWordCount(story.word_count)}</span>
          : <span title="Word count not in metadata" className="card__meta-muted">📄 —</span>}
        <span className="dot">·</span>
        <span title="Chapters">{chapterDisplay(story.chapter_count, story.chapter_count_total)} ch</span>
        {story.kudos > 0 && <><span className="dot">·</span><span title="Kudos">♥ {formatNumber(story.kudos)}</span></>}
        {story.hits > 0 && <><span className="dot">·</span><span title="Hits">👁 {formatNumber(story.hits)}</span></>}
        {story.comments > 0 && <><span className="dot">·</span><span title="Comments">💬 {formatNumber(story.comments)}</span></>}
        {story.language && story.language !== "English" && <><span className="dot">·</span><span>{story.language}</span></>}
        {story.updated_at && <><span className="dot">·</span><span title="Last updated">{story.updated_at.split("T")[0]}</span></>}
      </div>

      {story.relationships.length > 0 && (
        <div className="card__ships">
          {story.relationships.map(r => (
            <Link key={r} href={`/?relationships=${encodeURIComponent(r)}`} className="tag tag--ship tag--clickable">{r}</Link>
          ))}
        </div>
      )}
      {story.tags.length > 0 && <TagList tags={story.tags} className="card__tags" />}
      {story.warnings.filter(w => w !== "No Archive Warnings Apply").length > 0 && (
        <div className="card__warnings">
          {story.warnings.filter(w => w !== "No Archive Warnings Apply").map(w =>
            <span key={w} className="tag tag--warn">{w}</span>)}
        </div>
      )}

      <div className="card__actions">
        {story.is_hosted ? (
          <Link href={`/story/${story.id}/chapter/1`} className="card-btn card-btn--primary">
            Read here
          </Link>
        ) : canImport ? (
          <button className="card-btn card-btn--primary" onClick={importToRead} disabled={importing}>
            {importing ? "Importing…" : importedId ? "✓ Opening…" : "Import & Read"}
          </button>
        ) : (
          <Link href={`/story/${story.id}`} className="card-btn card-btn--primary">Details</Link>
        )}
        {story.is_hosted && (
          <Link href={`/story/${story.id}`} className="card-btn">Details</Link>
        )}
        <a href={externalUrl} target="_blank" rel="noopener noreferrer" className="card-btn">
          {externalLabel}
        </a>
        <button className={`card-btn ${bookmarked ? "card-btn--on" : ""}`} onClick={toggleBookmark}
                aria-label={bookmarked ? "Remove bookmark" : "Bookmark"}>
          {bookmarked ? "★" : "☆"}
        </button>
      </div>
    </article>
  )
}

// ── Empty / loading states ────────────────────────────────────────────────────
function EmptyState({ onPick, onSurprise }: { onPick: (q: string) => void; onSurprise: () => void }) {
  const examples = [
    "ship:Draco/Hermione >100k complete",
    "fandom: Harry Potter marauders wip updated:2y",
    "fandom: Harry Potter -tag:fluff rating:M complete words:>50k",
  ]
  return (
    <div className="empty">
      <p className="empty__title">Search the fanfiction internet</p>
      <p className="empty__sub">AO3 · FF.net · FicAlley · and more — fresh AO3 results pulled in as you search</p>
      <div className="empty__examples">
        <p className="empty__examples-label">Try:</p>
        {examples.map(ex => (
          <button key={ex} className="empty__ex" onClick={() => onPick(ex)}>{ex}</button>
        ))}
      </div>
      <button className="empty__surprise" onClick={onSurprise}>🎲 Surprise me</button>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

// Drag-to-resize for the filter panel (desktop only).
//
// The panel was a fixed 220px, which is too narrow for long fandom names —
// "Harry Potter and the Cursed Child - Thorne & Rowling" wrapped to three
// lines — and too wide for anyone who mostly wants the results. The width is
// remembered, so it is set once rather than every visit.
//
// Not offered on touch: there the panel is a full-height drawer, where a drag
// handle would fight the swipe-to-close gesture and the width is the screen.
const SIDEBAR_MIN = 180
const SIDEBAR_MAX = 460
const SIDEBAR_DEFAULT = 220

function useSidebarResize() {
  const [sidebarWidth, setSidebarWidth] = useState<number | null>(null)
  const dragging = useRef(false)

  useEffect(() => {
    const saved = Number(localStorage.getItem("ficatlas:sidebar_w"))
    if (saved >= SIDEBAR_MIN && saved <= SIDEBAR_MAX) setSidebarWidth(saved)
  }, [])

  const commit = useCallback((w: number) => {
    const clamped = Math.round(Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, w)))
    setSidebarWidth(clamped)
    try { localStorage.setItem("ficatlas:sidebar_w", String(clamped)) } catch {}
  }, [])

  const startResize = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    // Pointer capture keeps the drag alive when the cursor outruns the handle,
    // which it will — a 6px target and a fast mouse part company immediately.
    if (window.matchMedia("(pointer: coarse)").matches) return
    e.preventDefault()
    dragging.current = true
    const el = e.currentTarget
    el.setPointerCapture(e.pointerId)
    const startX = e.clientX
    const startW = sidebarWidth ?? SIDEBAR_DEFAULT
    document.body.classList.add("is-resizing")

    const move = (ev: PointerEvent) => {
      if (dragging.current) commit(startW + (ev.clientX - startX))
    }
    const up = (ev: PointerEvent) => {
      dragging.current = false
      document.body.classList.remove("is-resizing")
      try { el.releasePointerCapture(ev.pointerId) } catch {}
      el.removeEventListener("pointermove", move)
      el.removeEventListener("pointerup", up)
      el.removeEventListener("pointercancel", up)
    }
    el.addEventListener("pointermove", move)
    el.addEventListener("pointerup", up)
    el.addEventListener("pointercancel", up)
  }, [sidebarWidth, commit])

  // A drag handle that only responds to dragging is unusable without a mouse.
  const onResizeKey = useCallback((e: React.KeyboardEvent) => {
    const w = sidebarWidth ?? SIDEBAR_DEFAULT
    if (e.key === "ArrowLeft") { e.preventDefault(); commit(w - 16) }
    if (e.key === "ArrowRight") { e.preventDefault(); commit(w + 16) }
    if (e.key === "Home") { e.preventDefault(); commit(SIDEBAR_DEFAULT) }
  }, [sidebarWidth, commit])

  const resetWidth = useCallback(() => commit(SIDEBAR_DEFAULT), [commit])

  return { sidebarWidth, startResize, onResizeKey, resetWidth }
}


function SearchPageInner() {
  const router     = useRouter()
  const pathname   = usePathname()
  const rawParams  = useSearchParams()
  const [, startTransition] = useTransition()
  const { sidebarWidth, startResize, onResizeKey, resetWidth } = useSidebarResize()

  const get = (k: string) => rawParams.get(k) ?? undefined

  // Search bar
  const [query,   setQuery]   = useState(get("q") ?? "")
  const [sites,   setSites]   = useState<string[]>(csv(get("sites") ?? "ao3,ffnet,fictionalley"))
  const [explicit, setExplicit] = useState(get("explicit") === "true")
  // Most bulk-imported rows carry no ship/character data at all. Off by default so
  // a ship filter returns stories that actually have that ship; on, it widens the
  // net to include stories whose metadata we simply never captured.
  const [includeUnknown, setIncludeUnknown] = useState(get("include_unknown") === "true")
  // Set by clicking an author's name: browse their whole catalogue across archives.
  const [authorFilter, setAuthorFilter] = useState(get("author") ?? "")
  // How multiple values inside one filter combine. "all" finds crossovers and
  // tag combinations; "any" is what you want when one thing is split across
  // several spellings, which is common for fandoms.
  const [matchMode, setMatchMode] = useState<"all" | "any">(
    get("match_mode") === "any" ? "any" : "all")

  // Include filters
  const [incFandoms,  setIncFandoms]  = useState(csv(get("fandoms")))
  const [incChars,    setIncChars]    = useState(csv(get("characters")))
  const [incShips,    setIncShips]    = useState(csv(get("relationships")))
  const [incTags,     setIncTags]     = useState(csv(get("tags")))
  const [incRatings,  setIncRatings]  = useState(csv(get("ratings")))
  const [incWarnings, setIncWarnings] = useState(csv(get("warnings")))
  const [incCats,     setIncCats]     = useState(csv(get("categories")))

  // Exclude filters
  const [excFandoms, setExcFandoms] = useState(csv(get("exclude_fandoms")))
  const [excChars,   setExcChars]   = useState(csv(get("exclude_characters")))
  const [excShips,   setExcShips]   = useState(csv(get("exclude_relationships")))
  const [excTags,    setExcTags]    = useState(csv(get("exclude_tags")))

  // More options
  const [status,       setStatus]       = useState<string[]>(csv(get("status")))
  const [crossovers,   setCrossovers]   = useState(get("crossovers") ?? "include")
  const [language,     setLanguage]     = useState(get("language") ?? "")
  const [wordMin,      setWordMin]      = useState<number | undefined>(get("word_count_min") ? Number(get("word_count_min")) : undefined)
  const [wordMax,      setWordMax]      = useState<number | undefined>(get("word_count_max") ? Number(get("word_count_max")) : undefined)
  const [updatedAfter, setUpdatedAfter] = useState(get("updated_after") ?? "")
  const [searchWithin, setSearchWithin] = useState("")
  const [sort,         setSort]         = useState(get("sort") ?? "relevance")
  const [page,         setPage]         = useState(Number(get("page") ?? 1))

  // Results
  const [results,      setResults]      = useState<SearchResponse | null>(null)
  const [error,        setError]        = useState<string | null>(null)
  const [loading,      setLoading]      = useState(false)
  const [liveCount,    setLiveCount]    = useState(0)
  const [parsedTokens, setParsedTokens] = useState<ParsedToken[]>([])
  const [refreshing,   setRefreshing]   = useState(false)
  // Tracks which query we've already auto-deepened for, so a thin-result search
  // pulls fresh AO3 data once without looping on every re-render.
  const autoDeepenedRef = useRef<string>("")
  // Auto-search-on-filter-change machinery (see effect below).
  const hasSearchedRef = useRef(false)
  const filterDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Mobile filter drawer (sidebar becomes a slide-out panel on phones)
  const [filtersOpen,  setFiltersOpen]  = useState(false)
  // Prevent background scroll while the drawer is open
  useEffect(() => {
    if (typeof document === "undefined") return
    document.body.style.overflow = filtersOpen ? "hidden" : ""
    return () => { document.body.style.overflow = "" }
  }, [filtersOpen])
  const [refreshMsg,   setRefreshMsg]   = useState<string | null>(null)
  const [importing,    setImporting]    = useState(false)
  const [importMsg,    setImportMsg]    = useState<string | null>(null)

  const detectedUrl = detectFicUrl(query)

  const importDetectedUrl = useCallback(async () => {
    if (!detectedUrl) return
    setImporting(true); setImportMsg(null)
    try {
      const API_BASE = ""  // relative — handled by Next.js rewrite to backend
      const fd = new FormData(); fd.append("url", detectedUrl.url)
      const r = await fetch(`${API_BASE}/api/library/import-url`, { method: "POST", body: fd })
      if (!r.ok) throw new Error(await r.text())
      const data = await r.json()
      setImportMsg(`Imported \"${data.title}\" — ${data.chapters} chapters`)
      setQuery(""); doSearch()
    } catch (e: any) {
      setImportMsg(`Import failed: ${e.message}`)
    } finally {
      setImporting(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detectedUrl])

  const refreshFromAO3 = useCallback(async () => {
    setRefreshing(true); setRefreshMsg(null)
    try {
      const API_BASE = ""  // relative — handled by Next.js rewrite to backend
      const fd = new FormData()
      const pq = parseQuery(query)
      if (pq.cleanText) fd.append("q", pq.cleanText)
      if (pq.fandoms.length) fd.append("fandom", pq.fandoms[0])
      fd.append("pages", "5")
      const r = await fetch(`${API_BASE}/api/library/refresh-ao3`, { method: "POST", body: fd })
      const data = await r.json()
      setRefreshMsg(`Found ${data.fetched} live results — ${data.newly_indexed} new added to index.`)
      doSearch(false)
    } catch (e: any) {
      setRefreshMsg(`Refresh failed: ${e.message}`)
    } finally {
      setRefreshing(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query])

  // Live parse for sidebar highlighting (does NOT mutate chip state — chips
  // only commit on doSearch submit via the merge in fandoms/etc. params).
  const parsedLive = parseQuery(query)
  const fromSearch = (key: string) => parsedLive.tokens.filter(t => t.key === key && !t.exclude).map(t => t.value)

  // Fire an AO3 feed poll on page load (server debounces to once / 10 min)
  useEffect(() => {
    const API_BASE = ""  // relative — handled by Next.js rewrite to backend
    fetch(`${API_BASE}/api/library/autopoll`, { method: "POST" }).catch(() => {})
  }, [])

  // Apply saved default sites / sort from settings on a fresh landing (no URL params)
  useEffect(() => {
    if (rawParams.toString()) return  // user arrived with explicit params; respect them
    const API_BASE = ""  // relative — handled by Next.js rewrite to backend
    fetch(`${API_BASE}/api/settings`).then(r => r.json()).then(s => {
      if (s.default_sites) setSites(s.default_sites.split(",").filter(Boolean))
      if (s.default_sort) setSort(s.default_sort)
      if (s.show_explicit === "true") setExplicit(true)
    }).catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === "/") {
        e.preventDefault()
        const el = document.querySelector<HTMLInputElement>(".search-input")
        el?.focus()
      }
      if (e.key === "?" && e.shiftKey) {
        const btn = document.querySelector<HTMLButtonElement>(".syntax-help__btn")
        btn?.click()
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [])

  const tog = (arr: string[], set: (v: string[]) => void, id: string) =>
    set(arr.includes(id) ? arr.filter(x => x !== id) : [...arr, id])

  // Serialize the current filter-panel state into search-bar query syntax, so the
  // bar always reflects exactly what's being searched (single source of truth). Free
  // text already in the bar (non-filter words) is preserved; structured filters are
  // rewritten from the panel. Multi-word values get quoted so they round-trip.
  const serializeFiltersToQuery = useCallback((): string => {
    const pq = parseQuery(query)
    const freeText = pq.cleanText.trim()
    const q = (v: string) => (/\s/.test(v) ? `"${v}"` : v)
    const parts: string[] = []
    incFandoms.forEach(v => parts.push(`fandom:${q(v)}`))
    incShips.forEach(v => parts.push(`ship:${q(v)}`))
    incChars.forEach(v => parts.push(`char:${q(v)}`))
    incTags.forEach(v => parts.push(`tag:${q(v)}`))
    excFandoms.forEach(v => parts.push(`-fandom:${q(v)}`))
    excShips.forEach(v => parts.push(`-ship:${q(v)}`))
    excChars.forEach(v => parts.push(`-char:${q(v)}`))
    excTags.forEach(v => parts.push(`-tag:${q(v)}`))
    if (incRatings.length) incRatings.forEach(v => parts.push(`rating:${v}`))
    if (status.length === 1) parts.push(status[0] === "complete" ? "complete" : "wip")
    // The parser only understands k/m-suffixed word counts (100k, 1m), not raw
    // digits — format accordingly so the bar round-trips back to the same filter.
    const wc = (n: number) => (n % 1_000_000 === 0 ? `${n / 1_000_000}m` : `${Math.round(n / 1000)}k`)
    if (wordMin != null && wordMax != null) parts.push(`words:${wc(wordMin)}-${wc(wordMax)}`)
    else if (wordMin != null) parts.push(`words:>${wc(wordMin)}`)
    else if (wordMax != null) parts.push(`words:<${wc(wordMax)}`)
    if (language) parts.push(`lang:${q(language)}`)
    return [freeText, ...parts].filter(Boolean).join(" ")
  }, [query, incFandoms, incShips, incChars, incTags, excFandoms, excShips,
      excChars, excTags, incRatings, status, wordMin, wordMax, language])

  // Build search params
  const buildParams = useCallback((pg: number): SearchParams => {
    const pq = parseQuery(query)
    const merge = (sidebar: string[], parsed: string[]) =>
      [...new Set([...sidebar, ...parsed.filter(v => !sidebar.includes(v))])]

    return {
      q:                     pq.cleanText || undefined,
      sites:                 joinCsv(sites),
      fandoms:               joinCsv(merge(incFandoms, pq.fandoms)),
      characters:            joinCsv(merge(incChars, pq.characters)),
      relationships:         joinCsv(merge(incShips, pq.relationships)),
      tags:                  joinCsv(merge(incTags, pq.tags)),
      ratings:               joinCsv(incRatings.length ? incRatings : pq.ratings) ?? (explicit ? undefined : "G,T,M,NR"),
      warnings:              joinCsv(merge(incWarnings, pq.warnings)),
      categories:            joinCsv(incCats),
      crossovers:            pq.crossovers ?? (crossovers !== "include" ? crossovers : undefined) as any,
      exclude_fandoms:       joinCsv([...excFandoms, ...pq.excFandoms]),
      exclude_characters:    joinCsv([...excChars, ...pq.excCharacters]),
      exclude_relationships: joinCsv([...excShips, ...pq.excRelationships]),
      exclude_tags:          joinCsv([...excTags, ...pq.excTags]),
      status:                status.length ? joinCsv(status) : (pq.status ?? undefined),
      language:              language || pq.language || undefined,
      word_count_min:        wordMin ?? pq.wordCountMin ?? undefined,
      word_count_max:        wordMax ?? pq.wordCountMax ?? undefined,
      updated_after:         updatedAfter || pq.updatedAfter || undefined,
      explicit,
      author:                authorFilter || undefined,
      match_mode:            matchMode,
      include_unknown:       includeUnknown || undefined,
      search_within:         searchWithin || undefined,
      sort,
      page:                  pg,
      per_page:              20,
    }
  }, [query, sites, explicit, includeUnknown, authorFilter, matchMode, incFandoms, incChars, incShips, incTags, incRatings,
      incWarnings, incCats, excFandoms, excChars, excShips, excTags,
      status, crossovers, language, wordMin, wordMax, updatedAfter, searchWithin, sort])

  const doSearch = useCallback(async (resetPage = true, explicitPage?: number) => {
    // explicitPage lets pagination pass the target page directly, avoiding the
    // stale-closure bug where setPage(p=>p+1) hadn't flushed before doSearch ran.
    hasSearchedRef.current = true   // enables debounced auto-search on filter changes
    const pg = explicitPage ?? (resetPage ? 1 : page)
    if (resetPage) setPage(1)
    else if (explicitPage) setPage(explicitPage)
    const p = buildParams(pg)

    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(p)) {
      if (v !== undefined && v !== null && v !== "") qs.set(k, String(v))
    }
    startTransition(() => router.push(`${pathname}?${qs.toString()}`, { scroll: false }))

    setLoading(true)
    setError(null)

    // Save to recent searches
    if (query.trim()) {
      const recents = JSON.parse(localStorage.getItem("ficatlas:recent-searches") ?? "[]")
      const next = [query.trim(), ...recents.filter((q: string) => q !== query.trim())].slice(0, 20)
      localStorage.setItem("ficatlas:recent-searches", JSON.stringify(next))
    }

    try {
      let data
      try {
        data = await searchStories({ ...p, live: true } as any)
      } catch (liveErr) {
        // The live-AO3 augmentation can occasionally make the request fail
        // (e.g. AO3 timing out hard upstream). Fall back to an index-only search
        // so the user still gets indexed results instead of an error screen.
        data = await searchStories({ ...p, live: false } as any)
      }
      setResults(data)
      setLiveCount((data as any).live_count ?? 0)
      setParsedTokens((data as any).parsed_tokens ?? [])
      // Scroll to top of results on page change for a clean reading position
      if (explicitPage && explicitPage > 1) {
        window.scrollTo({ top: 0, behavior: "smooth" })
      }

      // Auto-deepen: a casual searcher just wants fics. If this query returned
      // very few results, AO3 is a selected site, and we haven't already deepened
      // for this exact query, pull a deeper live batch once and re-search. The
      // guard ref prevents looping. Only on a real query (page 1, has text/fandom).
      const indexedCount = (data as any).total ?? 0
      const thin = indexedCount < 5
      const hasQuery = (pg === 1) && (query.trim().length > 0 || (p as any).fandoms)
      if (thin && hasQuery && sites.includes("ao3")
          && autoDeepenedRef.current !== query && !refreshing) {
        autoDeepenedRef.current = query
        refreshFromAO3()   // pulls 5 pages from AO3, persists, then re-searches
      }
    } catch (e: any) {
      setError(e?.message ? `Search error: ${e.message}` : "Search failed — please try again.")
    } finally {
      setLoading(false)
    }
  }, [buildParams, page, pathname, router, query, sites, refreshing])

  // When a sidebar filter changes: mirror the full filter state into the search
  // bar (so the bar is the single visible source of truth — "replace" model).
  // The bar text updates immediately on every change; the *search* only auto-runs
  // once the user has already searched at least once (so we don't fire on the
  // empty landing page). Debounced so dragging through checkboxes isn't spammy.
  useEffect(() => {
    // Build the serialized query from current filter state.
    const serialized = serializeFiltersToQuery()
    // Only overwrite the bar when the panel actually contributes something, or
    // when it has just been cleared back to matching the bar — avoids stomping
    // free-text the user typed before touching any filter.
    setQuery(prev => (prev === serialized ? prev : serialized))

    if (!hasSearchedRef.current) return  // don't auto-search on the landing page
    if (filterDebounceRef.current) clearTimeout(filterDebounceRef.current)
    filterDebounceRef.current = setTimeout(() => { doSearch() }, 350)
    return () => { if (filterDebounceRef.current) clearTimeout(filterDebounceRef.current) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sites, incFandoms, incChars, incShips, incTags, incRatings, incWarnings,
      incCats, excFandoms, excChars, excShips, excTags, status, crossovers,
      language, wordMin, wordMax, updatedAfter, explicit, includeUnknown, authorFilter,
      matchMode, sort])

  // Append a syntax fragment from the help panel and put the caret after it, so
  // an operator like "fandom:" is ready to be typed into rather than merely
  // shown. The panel stays open — picking two or three in a row is the normal
  // way to build a query.
  const insertSyntax = useCallback((fragment: string) => {
    setQuery(prev => {
      const base = prev.trimEnd()
      return base ? `${base} ${fragment}` : fragment
    })
    // Focus after the state flush so the caret lands at the end.
    setTimeout(() => {
      const input = document.querySelector<HTMLInputElement>(".search-input")
      if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length) }
    }, 0)
  }, [])

  const removeToken = (raw: string) =>
    setQuery(q => q.replace(raw, "").replace(/\s+/g, " ").trim())

  const surpriseMe = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const API_BASE = ""
      // Use the current fandom filter if the user has one set, for relevant surprises.
      const params = new URLSearchParams({ count: "8" })
      if (incFandoms.length > 0) params.set("fandom", incFandoms[0])
      const r = await fetch(`${API_BASE}/api/search/random?${params.toString()}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const cards = await r.json()
      setResults({
        total: cards.length, page: 1, per_page: cards.length,
        results: cards, sites_searched: [], live_count: 0,
      } as any)
      setLiveCount(0); setParsedTokens([])
      window.scrollTo({ top: 0, behavior: "smooth" })
    } catch (e: any) {
      setError(e.message || "Couldn't fetch random stories")
    } finally {
      setLoading(false)
    }
  }, [incFandoms])

  const totalPages = results ? Math.ceil(results.total / results.per_page) : 0

  // Count active filters for the mobile drawer badge
  const activeFilterCount =
    incFandoms.length + incChars.length + incShips.length + incTags.length +
    excFandoms.length + excChars.length + excShips.length + excTags.length +
    (wordMin != null ? 1 : 0) + (wordMax != null ? 1 : 0) +
    incRatings.length + status.length +
    (searchWithin ? 1 : 0)

  return (
    <div className="shell">
      {/* ── Header ── */}
      <SiteHeader current="search">
        <label className="toggle-label">
          <input type="checkbox" checked={explicit} onChange={e => setExplicit(e.target.checked)} className="sr-only" />
          <span className="toggle-track"><span className="toggle-thumb" /></span>
          <span>Explicit</span>
        </label>
      </SiteHeader>

      <div className="layout">
        {/* Mobile filter backdrop — tap to close the drawer */}
        {filtersOpen && <div className="sidebar-backdrop" onClick={() => setFiltersOpen(false)} />}

        {/* ── Sidebar (slide-out drawer on mobile) ── */}
        <aside className={`sidebar ${filtersOpen ? "sidebar--open" : ""}`}
          style={sidebarWidth ? ({ ["--sidebar-w" as any]: `${sidebarWidth}px` }) : undefined}>

          <div className="sidebar__mobile-head">
            <span>Filters</span>
            <button className="sidebar__close" onClick={() => setFiltersOpen(false)} aria-label="Close filters">✕</button>
          </div>
          <div className="sidebar__top">
            <label className="sidebar__label">
              Sort
              <HelpTip label="About sorting">
                <strong>Relevance</strong> ranks by how well a story matches
                your words. The date sorts fall back to the published date when
                a work has no update date recorded, which is common in the bulk
                imports.
              </HelpTip>
            </label>
            <select value={sort} onChange={e => setSort(e.target.value)} className="select">
              {SORT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>

          <div className="sidebar__group">
            <label className="sidebar__label">
              Sites
              <HelpTip label="About the archives">
                Which archives to search. Works cross-posted to more than one
                site are collapsed into a single result, so unticking a site
                hides copies rather than losing the story.
              </HelpTip>
            </label>
            <Pills options={SITE_OPTIONS} selected={sites}
              onToggle={id => tog(sites, setSites, id)}
              highlighted={fromSearch("sites")} />
          </div>

          <div className="sidebar__group">
            <label className="sidebar__label">
              Match multiple values
              <HelpTip label="About matching multiple values">
                When you pick two or more fandoms, ships or tags:
                <strong> All of them</strong> needs a story to carry every one —
                that is how you find crossovers.
                <strong> Any of them</strong> needs just one, which reunites a
                fandom split across spellings, like the three separate
                &ldquo;Harry Potter&rdquo; tags.
              </HelpTip>
            </label>
            <div className="match-mode">
              <button className={`match-mode__btn ${matchMode === "all" ? "match-mode__btn--on" : ""}`}
                onClick={() => setMatchMode("all")}
                title="A story must have EVERY value you pick — use this to find crossovers.">
                All of them
              </button>
              <button className={`match-mode__btn ${matchMode === "any" ? "match-mode__btn--on" : ""}`}
                onClick={() => setMatchMode("any")}
                title="A story needs just ONE of the values — use this when a fandom is split across several spellings.">
                Any of them
              </button>
            </div>
            <p className="match-mode__hint">
              {matchMode === "all"
                ? "Stories carrying every value you pick — finds crossovers."
                : "Stories carrying any one value — combines split or variant tags."}
            </p>
          </div>

          <div className="sidebar__group">
            <label className="checkbox-row">
              <input type="checkbox" checked={includeUnknown}
                onChange={e => setIncludeUnknown(e.target.checked)} />
              <span>Include stories with missing info</span>
            </label>
            <HelpTip label="About stories with missing info">
              Most bulk-imported stories carry no ship or character tags at all.
              They are normally left out of those filters, so a ship filter
              returns only stories that genuinely match it. Tick this to widen
              the search back to rows whose metadata was never captured — more
              results, less certainty.
            </HelpTip>
          </div>

          <hr className="sidebar__rule" />
          <p className="sidebar__section-head">Include</p>

          <FilterSection label="Ratings" defaultOpen highlighted={fromSearch("ratings").length > 0} count={incRatings.length}>
            <Pills
              options={RATING_OPTIONS.filter(r => explicit || r.id !== "E")}
              selected={incRatings}
              onToggle={id => tog(incRatings, setIncRatings, id)}
              highlighted={fromSearch("ratings")} />
          </FilterSection>

          <FilterSection label="Archive Warnings" highlighted={fromSearch("warnings").length > 0} count={incWarnings.length}>
            {AO3_WARNINGS.map(w => (
              <label key={w} className={`check-row ${fromSearch("warnings").includes(w) ? "check-row--lit" : ""}`}>
                <input type="checkbox" checked={incWarnings.includes(w)}
                  onChange={() => tog(incWarnings, setIncWarnings, w)} />
                <span>{w}</span>
              </label>
            ))}
          </FilterSection>

          <FilterSection label="Categories" highlighted={fromSearch("categories").length > 0} count={incCats.length}>
            <Pills options={CATEGORIES.map(c => ({ id: c, label: c }))}
              selected={incCats} onToggle={id => tog(incCats, setIncCats, id)}
              highlighted={fromSearch("categories")} />
          </FilterSection>

          <FilterSection label="Fandoms" highlighted={parsedLive.fandoms.length > 0} count={incFandoms.length}>
            <TagInput value={incFandoms} onChange={setIncFandoms}
              placeholder="e.g. Harry Potter" highlighted={parsedLive.fandoms} kind="fandom" />
          </FilterSection>

          <FilterSection label="Relationships" highlighted={parsedLive.relationships.length > 0} count={incShips.length}>
            <TagInput value={incShips} onChange={setIncShips}
              placeholder="e.g. Draco/Hermione" highlighted={parsedLive.relationships} kind="relationship" />
          </FilterSection>

          <FilterSection label="Characters" highlighted={parsedLive.characters.length > 0} count={incChars.length}>
            <TagInput value={incChars} onChange={setIncChars}
              placeholder="e.g. Hermione Granger" highlighted={parsedLive.characters} kind="character" />
          </FilterSection>

          <FilterSection label="Additional Tags" highlighted={parsedLive.tags.length > 0} count={incTags.length}>
            <TagInput value={incTags} onChange={setIncTags}
              placeholder="e.g. slow burn" highlighted={parsedLive.tags} kind="tag" />
          </FilterSection>

          <FilterSection label="Curation" count={
            (incTags.includes("dlp_library") ? 1 : 0) +
            (incTags.includes("hpffa_archive") ? 1 : 0)
          }>
            <label className="curated-check">
              <input type="checkbox"
                checked={incTags.includes("dlp_library")}
                onChange={e => setIncTags(t => e.target.checked
                  ? [...t, "dlp_library"]
                  : t.filter(x => x !== "dlp_library"))} />
              <span>DLP curated only</span>
            </label>
            <label className="curated-check">
              <input type="checkbox"
                checked={incTags.includes("hpffa_archive")}
                onChange={e => setIncTags(t => e.target.checked
                  ? [...t, "hpffa_archive"]
                  : t.filter(x => x !== "hpffa_archive"))} />
              <span>HPFFA archive only</span>
            </label>
          </FilterSection>

          <hr className="sidebar__rule" />
          <p className="sidebar__section-head">Exclude</p>

          <FilterSection label="Fandoms" highlighted={parsedLive.excFandoms.length > 0} count={excFandoms.length}>
            <TagInput value={excFandoms} onChange={setExcFandoms}
              placeholder="Exclude fandom…" highlighted={parsedLive.excFandoms} kind="fandom" />
          </FilterSection>

          <FilterSection label="Relationships" highlighted={parsedLive.excRelationships.length > 0} count={excShips.length}>
            <TagInput value={excShips} onChange={setExcShips}
              placeholder="Exclude pairing…" highlighted={parsedLive.excRelationships} kind="relationship" />
          </FilterSection>

          <FilterSection label="Characters" highlighted={parsedLive.excCharacters.length > 0} count={excChars.length}>
            <TagInput value={excChars} onChange={setExcChars}
              placeholder="Exclude character…" highlighted={parsedLive.excCharacters} kind="character" />
          </FilterSection>

          <FilterSection label="Additional Tags" highlighted={parsedLive.excTags.length > 0} count={excTags.length}>
            <TagInput value={excTags} onChange={setExcTags}
              placeholder="Exclude tag…" highlighted={parsedLive.excTags} kind="tag" />
          </FilterSection>

          <hr className="sidebar__rule" />
          <p className="sidebar__section-head">More Options</p>

          <FilterSection label="Completion Status" highlighted={!!parsedLive.status} count={status.length}>
            <Pills
              options={[{ id: "complete", label: "Complete" }, { id: "in_progress", label: "In Progress" }, { id: "abandoned", label: "Abandoned" }]}
              selected={status} onToggle={id => tog(status, setStatus, id)}
              highlighted={parsedLive.status ? [parsedLive.status] : []} />
          </FilterSection>

          <FilterSection label="Crossovers">
            <div className="radio-group">
              {["include", "exclude", "only"].map(o => (
                <label key={o} className="radio-row">
                  <input type="radio" name="crossovers" value={o} checked={crossovers === o} onChange={() => setCrossovers(o)} />
                  <span>{o.charAt(0).toUpperCase() + o.slice(1)}</span>
                </label>
              ))}
            </div>
          </FilterSection>

          <FilterSection label="Word Count" highlighted={parsedLive.wordCountMin != null || parsedLive.wordCountMax != null}>
            <div className="pills" style={{marginBottom: "8px"}}>
              {WORD_COUNT_PRESETS.map(p => (
                <button key={p.label}
                  onClick={() => { setWordMin(p.min); setWordMax(p.max) }}
                  className={`pill ${wordMin === p.min && wordMax === p.max ? "pill--on" : ""}`}>
                  {p.label}
                </button>
              ))}
            </div>
            <div className="input-pair">
              <input type="number" placeholder="Min" value={wordMin ?? parsedLive.wordCountMin ?? ""}
                className="input-sm" onChange={e => setWordMin(e.target.value ? Number(e.target.value) : undefined)} />
              <span className="input-pair__sep">–</span>
              <input type="number" placeholder="Max" value={wordMax ?? parsedLive.wordCountMax ?? ""}
                className="input-sm" onChange={e => setWordMax(e.target.value ? Number(e.target.value) : undefined)} />
            </div>
          </FilterSection>

          <FilterSection label="Date Updated" highlighted={!!parsedLive.updatedAfter}>
            <div className="pills" style={{marginBottom: "8px"}}>
              {DATE_PRESETS.map(p => (
                <button key={p.label}
                  onClick={() => setUpdatedAfter(p.value ?? "")}
                  className={`pill ${updatedAfter === (p.value ?? "") ? "pill--on" : ""}`}>
                  {p.label}
                </button>
              ))}
            </div>
            <input type="date" value={updatedAfter || parsedLive.updatedAfter || ""}
              className="input-sm w-full" onChange={e => setUpdatedAfter(e.target.value)} />
          </FilterSection>

          <FilterSection label="Language" highlighted={!!parsedLive.language}>
            <input type="text" placeholder="e.g. English, Français…"
              value={language || parsedLive.language || ""}
              onChange={e => setLanguage(e.target.value)} className="input-sm w-full" />
          </FilterSection>

          <hr className="sidebar__rule" />
          <div className="sidebar__group">
            <label className="sidebar__label">Search within results</label>
            <input type="text" placeholder="Narrow current results…"
              value={searchWithin} onChange={e => setSearchWithin(e.target.value)}
              className="input-sm w-full" />
          </div>

          {/* Mobile-only: apply filters and close the drawer */}
          <div className="sidebar__mobile-apply">
            <button className="btn btn--primary w-full"
              onClick={() => { setFiltersOpen(false); doSearch() }}>
              Apply filters
            </button>
          </div>
        </aside>

        {/* Drag handle. A SIBLING of the sidebar, not a child: the sidebar is a
            scroll container (overflow-y: auto), which clips an absolutely
            positioned child sitting at its edge and scrolls it away with the
            content — so the handle existed but could not be seen or grabbed.
            As a flex item between the panel and the results it is simply
            always there. Desktop only; hidden by CSS on touch. */}
        <div
          className="sidebar__resizer"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize filter panel"
          aria-valuenow={sidebarWidth ?? 220}
          aria-valuemin={180}
          aria-valuemax={460}
          tabIndex={0}
          onPointerDown={startResize}
          onDoubleClick={resetWidth}
          onKeyDown={onResizeKey}
          title="Drag to resize · double-click to reset · ← → to nudge"
        />

        {/* ── Main ── */}
        <main className="main">
          {/* Search bar */}
          <div className="search-wrap">
            {detectedUrl && (
              <div className="url-detected">
                <span className="url-detected__icon">↓</span>
                <div className="url-detected__text">
                  <strong>{detectedUrl.site === "ao3" ? "AO3" : "FF.net"} story detected</strong>
                  <span className="url-detected__sub">We'll fetch the full text via FicHub and add it to your library — readable in-app, fully searchable.</span>
                </div>
                <button onClick={importDetectedUrl} disabled={importing} className="btn btn--primary">
                  {importing ? "Importing…" : "Import"}
                </button>
              </div>
            )}
            {importMsg && <div className="alert alert--success" style={{marginBottom:8}}>{importMsg}</div>}
            <div className="search-bar">
              <div className="search-input-wrap">
                <input type="text" className="search-input"
                  placeholder="fandom: Harry Potter  ship:Draco/Hermione  >100k  complete"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && doSearch()} />
                {query && (
                  <button className="search-clear" aria-label="Clear search text"
                    title="Clear the search box (keeps your filters)"
                    onClick={() => { setQuery(""); setTimeout(() => doSearch(), 0) }}>✕</button>
                )}
                <SyntaxHelp onInsert={insertSyntax} />
              </div>
              <button className="search-btn" onClick={() => doSearch()} disabled={loading}>
                {loading ? <span className="search-btn__spinner" /> : "Search"}
              </button>
            </div>
            <TokenStrip tokens={parsedTokens} onRemove={raw => {
              setQuery(q => q.replace(raw, "").replace(/\s+/g, " ").trim())
            }} />
          </div>

          {/* Mobile-only filters trigger — opens the slide-out filter drawer */}
          <button className="filters-trigger" onClick={() => setFiltersOpen(true)}>
            <span className="filters-trigger__icon">⚙</span> Filters &amp; sort
            {activeFilterCount > 0 && <span className="filters-trigger__badge">{activeFilterCount}</span>}
          </button>

          {error && <div className="alert alert--error">{error}</div>}

          {loading && !results && (
            <div className="story-list" aria-busy="true" aria-label="Loading results">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="card-skeleton">
                  <div className="skel-line skel-line--title" />
                  <div className="skel-line" />
                  <div className="skel-line skel-line--short" />
                  <div className="skel-line skel-line--meta" />
                </div>
              ))}
            </div>
          )}

          {results && (
            <>
              <div className="results-bar">
                <span className="results-bar__count">
                  {/* The backend counts to a ceiling of 5000 and stops, so a
                      capped total literally arrives as 5001. Printing that as
                      "5,001+" leaked the implementation and read as a precise
                      figure; it means "more than 5,000". */}
                  <strong>
                    {results.count_is_capped
                      ? `${(5000).toLocaleString()}+`
                      : results.total.toLocaleString()}
                  </strong>{" "}
                  {results.total === 1 && !results.count_is_capped ? "story" : "stories"}
                  {/* Which slice of them you are actually looking at. */}
                  {results.total > results.per_page && (
                    <span className="results-bar__range">
                      {" "}· showing {((results.page - 1) * results.per_page + 1).toLocaleString()}–
                      {Math.min(results.page * results.per_page,
                                results.count_is_capped ? 5000 : results.total).toLocaleString()}
                    </span>
                  )}
                  {results.sites_searched.length > 0 && ` · ${results.sites_searched.map(s => SITE_LABELS[s] ?? s).join(" + ")}`}
                  {liveCount > 0 && <span className="results-bar__live"> +{liveCount} live</span>}
                  {/* Without this it looks like the filters are broken: results
                      appear that have no value for the field being filtered. */}
                  {includeUnknown && (
                    <button className="results-bar__loose"
                      onClick={() => setIncludeUnknown(false)}
                      title="Results include stories with no data for the fields you filtered on. Click to show only confirmed matches.">
                      · incl. missing info ✕
                    </button>
                  )}
                </span>
                <span className="results-bar__actions">
                  {sites.includes("ao3") && (
                    <button className="page-btn page-btn--refresh" onClick={refreshFromAO3} disabled={refreshing} title="Pull fresh AO3 results and add them to the index">
                      {refreshing ? "Refreshing…" : "↻ Refresh from AO3"}
                    </button>
                  )}
                  <span className="results-bar__page">Page {results.page} of {totalPages}</span>
                </span>
              </div>
              {refreshMsg && <div className="alert alert--success" style={{marginBottom:10}}>{refreshMsg}</div>}

              <div className="story-list">
                {results.results.length === 0 ? (
                  <div className="no-results">
                    <p className="no-results__title">No stories matched</p>
                    {/* Ship/character/tag data is missing for most bulk-imported
                        stories, so a strict filter on those is the likeliest reason
                        for an empty page. Say so, and offer the fix directly. */}
                    {!includeUnknown && (incShips.length || incChars.length || incTags.length) ? (
                      <>
                        <p className="no-results__sub">
                          No indexed story lists {incShips.length ? "that relationship"
                            : incChars.length ? "that character" : "that tag"}.
                          Most imported stories carry no {incShips.length ? "relationship"
                            : incChars.length ? "character" : "tag"} data at all, so they are
                          excluded from this filter.
                        </p>
                        <button className="btn btn--primary no-results__fetch"
                          onClick={() => setIncludeUnknown(true)}>
                          Include stories with missing info
                        </button>
                      </>
                    ) : (
                      <p className="no-results__sub">
                        Try removing a filter, broadening the word count, or checking a different site.
                      </p>
                    )}
                    {sites.includes("ao3") && (
                      <button className="btn btn--primary no-results__fetch"
                        onClick={refreshFromAO3} disabled={refreshing}>
                        {refreshing ? "Searching AO3…" : "🔍 Search AO3 directly for this"}
                      </button>
                    )}
                    {refreshMsg && <p className="no-results__refresh-msg">{refreshMsg}</p>}
                    <p className="no-results__hint">
                      Have a specific story in mind? Paste its AO3 or FF.net URL into the search bar to import it.
                    </p>
                  </div>
                ) : (
                  results.results.map(s => <StoryCard key={s.id} story={s} />)
                )}
              </div>

              {totalPages > 1 && (
                <div className="pagination">
                  <button disabled={page <= 1}
                    onClick={() => doSearch(false, Math.max(1, page - 1))}
                    className="page-btn">← Previous</button>
                  <span className="pagination__info">Page {page} of {totalPages}</span>
                  <button disabled={page >= totalPages}
                    onClick={() => doSearch(false, page + 1)}
                    className="page-btn">Next →</button>
                </div>
              )}
            </>
          )}

          {!results && !loading && <EmptyState
            onPick={(q) => { setQuery(q); setTimeout(() => doSearch(), 0) }}
            onSurprise={surpriseMe} />}
        </main>
      </div>
    </div>
  )
}

export default function SearchPage() {
  return (
    <Suspense fallback={null}>
      <SearchPageInner />
    </Suspense>
  )
}
