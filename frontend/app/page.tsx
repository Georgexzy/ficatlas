"use client"
export const dynamic = "force-dynamic"
import { useState, useCallback, useMemo, useTransition, useEffect, useRef, Suspense } from "react"
import { useRouter, useSearchParams, usePathname } from "next/navigation"
import Link from "next/link"
import OfflineLink from "./OfflineLink"
import HelpTip from "./HelpTip"
import type { SearchParams, SearchResponse, StoryCard } from "@/lib/types"
import { searchStories, formatWordCount, formatNumber, chapterDisplay,
         SITE_LABELS, RATING_LABELS, SORT_OPTIONS, WORD_COUNT_PRESETS, formatStoryDate,
         DATE_PRESETS, AO3_WARNINGS, CATEGORIES, LANGUAGE_OPTIONS, getIndexTotals, FICALLEY_SECTIONS, coverageWarning } from "@/lib/api"
import { parseQuery, parsedToSearchParams, type ParsedToken } from "@/lib/queryParser"
import { storyLink, isSeedUrl } from "@/lib/storyLinks"
import SyntaxHelp from "./SyntaxHelp"
import { rememberSearch } from "@/lib/lastSearch"
import { describeError, type Failure } from "@/lib/errors"
import { readAllPrefs, type Prefs } from "@/lib/prefs"
import WordCountSlider from "./WordCountSlider"
import DlpStars, { dlpRating } from "./DlpStars"
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
function TagList({ tags, className, kind = "tags", tagClass = "" }: {
  tags: string[]; className?: string
  kind?: "tags" | "fandoms" | "relationships" | "characters"
  tagClass?: string
}) {
  const [expanded, setExpanded] = useState(false)
  const shown = expanded ? tags : tags.slice(0, 5)
  const extra = tags.length - 5
  return (
    <div className={`tag-list ${className ?? ""}`}>
      {shown.map(t => (
        <Link key={t} href={`/?${kind}=${encodeURIComponent(t)}`}
          className={`tag tag--clickable ${tagClass}`}>{t}</Link>
      ))}
      {!expanded && extra > 0 && <button onClick={() => setExpanded(true)} className="tag tag--more">+{extra}</button>}
      {expanded  && extra > 0 && <button onClick={() => setExpanded(false)} className="tag tag--more">less</button>}
    </div>
  )
}

// ── Collapsible filter section ────────────────────────────────────────────────
function FilterSection({ label, children, defaultOpen = false, highlighted = false,
                        count = 0, note = null }: {
  label: string; children: React.ReactNode; defaultOpen?: boolean; highlighted?: boolean
  count?: number
  /** Shown inside the section when the filter is mostly inert for the current
   *  site selection. Deliberately inside rather than beside the heading: a
   *  collapsed section should not shout, and by the time you have opened it you
   *  are about to use the thing the note is about. */
  note?: string | null
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
      {open && (
        <div className="filter-section__body">
          {note && <p className="filter-section__note">{note}</p>}
          {children}
        </div>
      )}
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
  const { user } = useAuth()
  // Tags that are not already shown as a ship or a character above, and not the
  // internal dlp_stars marker (rendered as stars in the badge row).
  const freeformTags = useMemo(() => {
    const shown = new Set(
      [...(story.relationships ?? []), ...(story.characters ?? [])]
        .map(v => v.toLowerCase()),
    )
    return (story.tags ?? []).filter(
      t => !t.startsWith("dlp_stars:") && !shown.has(t.toLowerCase()),
    )
  }, [story.tags, story.relationships, story.characters])

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
  //
  // Also gated on the viewer's role. /api/library/import-url requires admin, so
  // showing this to a logged-out visitor offered a button whose only possible
  // outcome was a 401 alert. They get Details instead, which works for everyone.
  const canImport = user?.can_import
    && !story.is_hosted && !isSeedUrl(story.url)
    && (story.site === "ao3" || story.site === "ffnet")

  return (
    <article className="card">
      <div className="card__top">
        <div className="card__title-row">
          <Link href={`/story/${story.id}`} className="card__title">{story.title}</Link>
          <div className="card__badges">
            <span className={`badge badge--site-${story.site}`}>{SITE_LABELS[story.site] ?? story.site}</span>
            {/* Only ever set in an admin's results — the API drops delisted rows
                for everyone else. Loud on purpose: this card is invisible to
                every reader, and an operator scanning results needs to know
                that at a glance rather than wondering why nobody can find it. */}
            {story.delisted && (
              <span className="badge badge--delisted"
                title="Removed from the public index at the author's request. Only you can see this.">
                Delisted
              </span>
            )}
            {/* FictionAlley was five archives behind one banner and readers
                navigated by them, so the section is as identifying as the
                site name. Clickable, like every other facet on a card. */}
            {story.archive_section && (
              <Link href={`/?sections=${encodeURIComponent(story.archive_section)}&sites=fictionalley`}
                className="badge badge--section"
                title={`Browse the ${story.archive_section} section of FictionAlley`}>
                {story.archive_section}
              </Link>
            )}
            {/* Series, where the work is in one. On the card rather than only
                on the story page, because "part 3 of 5" changes whether you
                click at all — nobody wants to start in the middle. */}
            {story.series_name && story.series_id && (
              <Link href={`/series/${story.series_id}`} className="badge badge--series"
                title={`${story.series_name} — see the whole series in reading order`}>
                {story.series_position ? `#${story.series_position} ` : ""}
                {story.series_name}
              </Link>
            )}
            {story.cross_post_urls && story.cross_post_urls.length > 0 && (
              <span className="badge badge--crosspost"
                title={`Also posted on:\n${story.cross_post_urls.join("\n")}`}>
                +{story.cross_post_urls.length} {story.cross_post_urls.length === 1 ? "copy" : "copies"}
              </span>
            )}
            {story.rating && <span className={`badge badge--rating badge--${story.rating.toLowerCase()}`}>{RATING_LABELS[story.rating] ?? story.rating}</span>}
            {story.status === "complete" && <span className="badge badge--complete">Complete</span>}
            {story.tags?.includes("dlp_library") && <span className="badge badge--dlp" title="Curated by DarkLordPotter">DLP</span>}
            {dlpRating(story.tags) != null && <DlpStars value={dlpRating(story.tags)!} />}
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
        {story.bookmarks > 0 && <><span className="dot">·</span><span title="Bookmarks">🔖 {formatNumber(story.bookmarks)}</span></>}
        {story.language && story.language !== "English" && <><span className="dot">·</span><span>{story.language}</span></>}

        {/* Say WHICH date it is. A bare "2026-01-11" does not distinguish when a
            story appeared from when it last changed, and those mean very
            different things when deciding whether to start a WIP. Fall back to
            the published date so a work we have a date for never shows none. */}
        {story.updated_at ? (
          <><span className="dot">·</span>
            <span title={`Last updated ${story.updated_at.split("T")[0]}`}>
              Updated {formatStoryDate(story.updated_at)}
            </span></>
        ) : story.published_at ? (
          <><span className="dot">·</span>
            <span title={`Published ${story.published_at.split("T")[0]}`}>
              Published {formatStoryDate(story.published_at)}
            </span></>
        ) : null}

        {/* Both dates are worth showing when a work has actually been revised —
            "published 2015, updated last month" is the shape of a long-running
            WIP and is invisible if you only ever see one of them. */}
        {story.updated_at && story.published_at
          && story.published_at.split("T")[0] !== story.updated_at.split("T")[0] && (
          <><span className="dot">·</span>
            <span className="card__meta-muted"
              title={`First published ${story.published_at.split("T")[0]}`}>
              pub. {new Date(story.published_at).getFullYear()}
            </span></>
        )}
      </div>

      {/* Capped like every other tag row. This mapped ALL relationships, and on
          a phone each one is a full-width line — a work with twenty ships
          printed twenty rows before the reader reached anything else. TagList
          shows five and expands on demand. */}
      {story.relationships.length > 0 && (
        <TagList tags={story.relationships} kind="relationships"
          className="card__ships" tagClass="tag--ship" />
      )}
      {/* dlp_stars is rendered as stars in the badge row above, so it must not
          also appear here as a literal "dlp_stars:4.67" chip. */}
      {/* Characters were collected but never shown on a card, so the only way
          to see who is in a story was to open it — and unlike AO3 they could
          not be clicked to search. They sit above the freeform tags because
          that is the order AO3 lists them in and it is the more useful signal. */}
      {story.characters.length > 0 && (
        <TagList tags={story.characters} kind="characters" className="card__chars" />
      )}
      {/* Freeform tags only.
          `tags` is stored as relationships + characters + freeforms, so
          rendering it whole alongside the dedicated ship and character rows
          printed every pairing and every character TWICE — a card for a
          well-tagged work was three-quarters duplicate text. Subtract what is
          already shown above and only the genuine freeform tags remain. */}
      {freeformTags.length > 0 && (
        <TagList tags={freeformTags} className="card__tags" />
      )}
      {story.warnings.filter(w => w !== "No Archive Warnings Apply").length > 0 && (
        <div className="card__warnings">
          {/* Clickable like every other facet — a warning is a thing you search
              FOR as often as one you avoid, and the exclude form is a click away
              in the sidebar. */}
          {story.warnings.filter(w => w !== "No Archive Warnings Apply").map(w =>
            <Link key={w} href={`/?warnings=${encodeURIComponent(w)}`}
              className="tag tag--warn tag--clickable"
              title={`Find works tagged "${w}"`}>{w}</Link>)}
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


// Recent searches, revealed by focusing an empty search box.
//
// First attempt put these in a permanent row under the bar, which is just
// clutter on every page load for something you want occasionally. They lived
// behind a Library tab before that, which was two clicks from the only place
// they are useful.
//
// Focus-triggered is how every search box on the web behaves, so it needs no
// explaining: click into an empty box and your recent searches are there;
// start typing and they get out of the way.
function RecentSearches(
  { open, onPick, onDismiss }:
  { open: boolean; onPick: (q: string) => void; onDismiss: () => void },
) {
  const [recents, setRecents] = useState<string[]>([])

  useEffect(() => {
    if (!open) return
    try {
      const raw = JSON.parse(localStorage.getItem("ficatlas:recent-searches") ?? "[]")
      setRecents(Array.isArray(raw) ? raw.filter(x => typeof x === "string").slice(0, 8) : [])
    } catch { setRecents([]) }
  }, [open])

  const clear = () => {
    setRecents([])
    try { localStorage.setItem("ficatlas:recent-searches", "[]") } catch {}
    onDismiss()
  }

  if (!open || !recents.length) return null
  return (
    <div className="recent-drop" role="listbox" aria-label="Recent searches">
      <div className="recent-drop__head">
        <span className="recent-drop__label">Recent searches</span>
        <button className="recent-drop__clear" onMouseDown={e => e.preventDefault()}
          onClick={clear}>Clear</button>
      </div>
      {recents.map(q => (
        <button
          key={q}
          role="option"
          aria-selected={false}
          className="recent-drop__item"
          // mousedown, not click: the input's blur fires first and would close
          // the dropdown out from under the pointer before click ever lands.
          onMouseDown={e => { e.preventDefault(); onPick(q) }}
          title={q}
        >
          <span className="recent-drop__icon" aria-hidden="true">↻</span>
          <span className="recent-drop__text">{q}</span>
        </button>
      ))}
    </div>
  )
}


// Shared with the index-status widget: 19,650,992 -> "19.7M".
function fmtCount(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`
  return String(n)
}

// ── Empty / loading states ────────────────────────────────────────────────────
// The example queries that used to sit here now live in the syntax panel under
// the search bar, where they are clickable and next to the box they fill in.
// On the landing page they were three lines of operator soup competing with the
// one thing to do — type something.
function EmptyState({ onSurprise }: { onSurprise: () => void }) {
  const [total, setTotal] = useState<number | null>(null)

  useEffect(() => {
    // Shared with the header widget — see getIndexTotals. Both wanting the same
    // number used to mean four requests per page load between them.
    getIndexTotals().then(d => { if (typeof d?.stories === "number") setTotal(d.stories) })
  }, [])

  return (
    <div className="empty">
      <p className="empty__title">Search the fanfiction internet</p>
      {/* Accurate about what the live fetch does. It runs AFTER the response is
          sent — indexed results come back in milliseconds and the fresh works
          land in the index for next time — so "pulled in as you search" was
          promising something the reader never sees in that search. */}
      {/* The count is read from the index, not written into the copy. It was
          hardcoded at "19.6M" and had already drifted — the header was showing
          19.7M on the same screen — and it only ever grows, so any literal here
          starts going stale the moment the workers add a row. */}
      <p className="empty__sub">
        {total ? `${fmtCount(total)} works` : "Millions of works"} from AO3,
        FanFiction.net and FicAlley — one search across all three, instead of
        three searches that each miss two.
      </p>
      <p className="empty__nudge">
        Type anything above, or press <kbd>?</kbd> in the search bar to see what you can filter by.
      </p>
      <button className="empty__surprise" onClick={onSurprise}>🎲 Surprise me</button>

      {/* Says the quiet part out loud, because for this audience it is not
          quiet at all. Fanfiction readers tie an archive's trustworthiness to
          exactly these properties: AO3's standing rests on being noncommercial,
          ad-free and volunteer-run, and FanFiction.net's decline is widely
          attributed in part to "rampant advertisements". A new fanfic site with
          no visible answer to "what's the catch" reads as one with a catch.

          Every claim here is enforced somewhere real rather than asserted: the
          licence is PolyForm Noncommercial, robots.txt refuses the AI training
          crawlers, and there is no analytics script in the bundle. */}
      <ul className="empty__promises">
        <li>No adverts, ever</li>
        <li>No tracking, no analytics</li>
        <li>Non-commercial &amp; <a href="https://github.com/Georgexzy/ficatlas"
              target="_blank" rel="noopener noreferrer">open source</a></li>
        <li>Links out to the original archive</li>
      </ul>
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


// The search bar's example was hardcoded to "fandom: Harry Potter
// ship:Draco/Hermione". That is fine if you read Harry Potter and faintly
// alienating if you do not — it makes a general-purpose index look like one
// person's shelf. Drawing it from the index's own biggest fandoms keeps the
// example honest as the data changes, and means the site never advertises a
// fandom it barely holds.
function useExampleQuery(): string {
  const [fandom, setFandom] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    fetch("/api/stats/suggest?kind=fandom&q=&limit=8")
      .then(r => r.json())
      .then((rows: { value: string }[]) => {
        if (!live || !Array.isArray(rows) || !rows.length) return
        // Skip AO3's disambiguated forms ("X - J. K. Rowling") — correct, but
        // long and noisy as a hint.
        const clean = rows.map(r => r.value).filter(v => !v.includes(" - ") && v.length < 26)
        const pick = (clean.length ? clean : rows.map(r => r.value))[
          Math.floor(Math.random() * (clean.length || rows.length))
        ]
        setFandom(pick ?? null)
      })
      .catch(() => {})
    return () => { live = false }
  }, [])
  return fandom
    ? `fandom: ${fandom}  >100k  complete`
    : "try a fandom, a ship, or just words from the summary"
}


function SearchPageInner() {
  const router     = useRouter()
  const pathname   = usePathname()
  const rawParams  = useSearchParams()
  const { user }   = useAuth()
  const exampleQuery = useExampleQuery()

  // FictionAlley sections, and the state behind their contextual help.
  // rawParams directly, not the get() helper: that is declared further down,
  // and reading it here put the component in the temporal dead zone —
  // "Cannot access 'ea' before initialization", which took the whole results
  // list out rather than failing visibly at the thing that caused it.
  const [sections, setSections] = useState<string[]>(
    (rawParams.get("sections") ?? "").split(",").filter(Boolean))

  // One bubble, always saying the same thing.
  //
  // It used to rewrite itself to describe whichever pill you were pointing at.
  // That reads well and is confusing to actually use: the bubble opens on a
  // click and the content changes on hover, so you point at Schnoogle, move
  // toward the "?", pass over Riddikulus on the way, and read about the wrong
  // one. Nothing on screen says the two are connected, and on a touch screen
  // there is no hover at all, so the feature simply did not exist there.
  //
  // Self-contained instead: what a subsite is, then all five with a line each.
  // Longer to read, but it answers the question on the first try and works the
  // same under a finger as under a mouse.
  const sectionHelpBody = (
    <>
      <p>FictionAlley was not one archive but <strong>five</strong>, and it
      shelved by <em>kind of story</em> rather than by tag — a Schnoogle fic
      meant novel-length, a Dark Arts fic meant horror.</p>
      <p>That makes these the closest thing its 30,000 works have to genre tags,
      and for most of them, which predate tagging as we know it, the only one.</p>
      <ul className="helptip__list">
        {FICALLEY_SECTIONS.map(sec => (
          <li key={sec.value}><strong>{sec.label}</strong> — {sec.short}</li>
        ))}
      </ul>
      <p className="helptip__aside">
        Shown only while FictionAlley is one of the sites above. Typing{" "}
        <code>subsite:Schnoogle</code> does the same as clicking it.
      </p>
    </>
  )
  const [, startTransition] = useTransition()
  const { sidebarWidth, startResize, onResizeKey, resetWidth } = useSidebarResize()

  const get = (k: string) => rawParams.get(k) ?? undefined

  // Search bar
  const [query,   setQuery]   = useState(get("q") ?? "")
  const [searchFocused, setSearchFocused] = useState(false)
  // Set when the READER changed the bar, cleared once that edit has been read
  // back into the filter state. Without it the two syncs fight: the panel
  // writes the bar, which would parse back into the panel, which rewrites the
  // bar. One flag makes the direction explicit per edit.
  const barEditedRef = useRef(false)
  const [sites,   setSites]   = useState<string[]>(csv(get("sites") ?? "ao3,ffnet,fictionalley"))
  const [explicit, setExplicit] = useState(get("explicit") === "true")
  // Was hard-coded to 20, so the Results-per-page setting had never once had an
  // effect. Seeded from the URL first — a shared link should show what the
  // sender saw, not what the recipient prefers.
  const [perPage, setPerPage] = useState(() => {
    const n = Number(get("per_page"))
    return Number.isFinite(n) && n > 0 && n <= 100 ? n : 20
  })
  // Most bulk-imported rows carry no ship/character data at all. Off by default so
  // a ship filter returns stories that actually have that ship; on, it widens the
  // net to include stories whose metadata we simply never captured.
  const [includeUnknown, setIncludeUnknown] = useState(get("include_unknown") === "true")
  // "", "yes" or "no" — three states, because "no preference" is the common one
  // and a checkbox cannot express it without meaning "standalones only".
  const [inSeries, setInSeries] = useState(get("in_series") === "true" ? "yes"
                                         : get("in_series") === "false" ? "no" : "")
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
  const [dlpMinRating, setDlpMinRating] = useState<number | undefined>(
    get("dlp_min_rating") ? Number(get("dlp_min_rating")) : undefined)
  const [wordMin,      setWordMin]      = useState<number | undefined>(get("word_count_min") ? Number(get("word_count_min")) : undefined)
  const [wordMax,      setWordMax]      = useState<number | undefined>(get("word_count_max") ? Number(get("word_count_max")) : undefined)
  const [updatedAfter, setUpdatedAfter] = useState(get("updated_after") ?? "")
  const [searchWithin, setSearchWithin] = useState("")
  const [sort,         setSort]         = useState(get("sort") ?? "relevance")
  const [page,         setPage]         = useState(Number(get("page") ?? 1))

  // Results
  const [results,      setResults]      = useState<SearchResponse | null>(null)
  const [error,        setError]        = useState<Failure | null>(null)
  const [loading,      setLoading]      = useState(false)
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
      const fd = new FormData()
      fd.append("url", detectedUrl.url)
      // Readers import to their own shelf; only an operator can publish into
      // the shared index. Sending private=false for everyone meant the button
      // 403'd for every reader while the label promised them a library.
      fd.append("private", String(!user?.can_manage))
      const r = await fetch(`${API_BASE}/api/library/import-url`,
                            { method: "POST", body: fd, credentials: "include" })
      if (!r.ok) throw new Error(await r.text())
      const data = await r.json()
      setImportMsg(user?.can_manage
        ? `Added "${data.title}" to the index — ${data.chapters} chapters, searchable by everyone.`
        : `"${data.title}" is on your shelf — ${data.chapters} chapters. Find it in Library › My shelf.`)
      setQuery("")
      if (user?.can_manage) doSearch()
    } catch (e: any) {
      setImportMsg(`Import failed: ${e.message}`)
    } finally {
      setImporting(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detectedUrl, user])

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

  // Fire an AO3 feed poll on page load (server debounces to once / 10 min).
  //
  // Admins only: the endpoint requires admin, so for everyone else this was a
  // guaranteed 401 on every single page load — a wasted round trip per visitor,
  // a red error in their console, and rate-limit budget spent on nothing.
  useEffect(() => {
    if (!user?.can_manage) return
    const API_BASE = ""  // relative — handled by Next.js rewrite to backend
    fetch(`${API_BASE}/api/library/autopoll`, { method: "POST" }).catch(() => {})
  }, [user?.can_manage])

  // Run the search when the URL already describes one.
  //
  // There was no on-mount search at all: doSearch fired only from a keypress,
  // the Search button or a filter change. So arriving at /?fandoms=Harry+Potter
  // — which is what every clickable fandom, character, tag, warning and author
  // link on a card produces, and what any shared or bookmarked search URL looks
  // like — filled the controls in and then showed the empty landing state until
  // you pressed Search yourself.
  //
  // Runs once on mount only. Later navigations within the app set state
  // directly and search through their own handlers.
  useEffect(() => {
    if (!rawParams.toString()) return
    const SEARCH_PARAMS = [
      "q", "fandoms", "relationships", "characters", "tags", "author",
      "ratings", "warnings", "categories", "status", "language",
      "word_count_min", "word_count_max", "updated_after", "sites",
      "dlp_min_rating", "exclude_fandoms", "exclude_tags",
      // Without this, a link like /?sections=Schnoogle&sites=fictionalley — which
      // is exactly what the section badges on result cards produce — landed on
      // the page and ran no search at all.
      "sections",
    ]
    if (!SEARCH_PARAMS.some(k => rawParams.get(k))) return

    // Every search ran TWICE, and had done since the keyed remount was added.
    // doSearch writes its parameters into the URL; the key is that URL; so the
    // component remounted and this effect ran the identical search again. On a
    // 19.7M-row index that is double the work for nothing, and it was invisible
    // because both requests returned the same thing.
    //
    // The remount still has to re-search when the URL changed because somebody
    // clicked a facet link. The difference is whether these parameters are
    // already what the current results reflect.
    hasSearchedRef.current = true    // filter tweaks queue from here on
    doSearch()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Apply saved default sites / sort from settings on a fresh landing (no URL params)
  useEffect(() => {
    if (rawParams.toString()) return  // user arrived with explicit params; respect them
    const API_BASE = ""  // relative — handled by Next.js rewrite to backend
    // This reader's own defaults win over the instance's. The server value is
    // what a first-time visitor gets; anything they have since chosen in
    // Settings is on the device and takes precedence. Applied without waiting
    // for the fetch, so a slow API cannot delay your own preference.
    const mine = readAllPrefs()
    const apply = (v: Partial<Prefs>) => {
      if (v.default_sites) setSites(v.default_sites.split(",").filter(Boolean))
      if (v.default_sort) setSort(v.default_sort)
      if (v.show_explicit !== undefined) setExplicit(v.show_explicit === "true")
      const n = Number(v.results_per_page)
      if (Number.isFinite(n) && n > 0 && n <= 100) setPerPage(n)
    }
    apply(mine)
    fetch(`${API_BASE}/api/settings`).then(r => r.json())
      .then(s => apply({ ...s, ...mine }))
      .catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // How much is currently narrowing the search, and a way out of all of it.
  //
  // "Exit" is a navigation to "/", not twenty-odd setState calls. Every filter
  // is already seeded from the URL and SearchPageKeyed remounts when the URL
  // changes, so an empty URL IS the clean state — resetting each piece by hand
  // would be a second definition of "empty" to keep in step with the first, and
  // the one that silently drifts.
  const activeFilters =
    incFandoms.length + incChars.length + incShips.length + incTags.length +
    incWarnings.length + incCats.length + sections.length + status.length +
    excFandoms.length + excChars.length + excShips.length + excTags.length +
    (language ? 1 : 0) + (authorFilter ? 1 : 0) + (wordMin ? 1 : 0) +
    (wordMax ? 1 : 0) + (updatedAfter ? 1 : 0) + (dlpMinRating ? 1 : 0) +
    (crossovers !== "include" ? 1 : 0) + (includeUnknown ? 1 : 0) +
    // Ratings only count as a filter when they are not the default set.
    (incRatings.length && incRatings.length < (explicit ? 5 : 4) ? 1 : 0) +
    (sites.length < 3 ? 1 : 0)
  const searchIsActive = query.trim().length > 0 || activeFilters > 0 || !!results

  const exitSearch = useCallback(() => {
    // Same target as the wordmark, so the two cannot disagree about what home
    // means. scroll:true because you are leaving, not paging.
    startTransition(() => router.push(pathname))
  }, [router, pathname, startTransition])

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === "/") {
        e.preventDefault()
        const el = document.querySelector<HTMLInputElement>(".search-input")
        el?.focus()
      }
      // Escape is the conventional way out of a mode, and search results ARE a
      // mode — everything on screen is about a query you have finished with.
      // Only when nothing is focused, so it never steals Escape from a text
      // field, the help panel or the mobile filter drawer.
      if (e.key === "Escape" && searchIsActive) {
        e.preventDefault()
        exitSearch()
        return
      }
      if (e.key === "?" && e.shiftKey) {
        const btn = document.querySelector<HTMLButtonElement>(".syntax-help__btn")
        btn?.click()
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [searchIsActive, exitSearch])

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
    // Sections were missing from the bar, so selecting Schnoogle narrowed the
    // results and the search box carried on claiming the old query — the one
    // place a reader looks to see what they have actually asked for.
    sections.forEach(v => parts.push(`subsite:${q(v)}`))
    // Only write ratings into the bar when they are a real narrowing. Having
    // every available rating selected IS the default, and spelling it out put
    // "rating:G rating:T rating:M rating:NR" in front of whatever the reader
    // actually searched for.
    const allRatings = RATING_OPTIONS.filter(r => explicit || r.id !== "E").map(r => r.id)
    const ratingsAreDefault =
      incRatings.length === 0 || incRatings.length === allRatings.length
    if (!ratingsAreDefault) incRatings.forEach(v => parts.push(`rating:${v}`))
    if (status.length === 1) parts.push(status[0] === "complete" ? "complete" : "wip")
    // The parser only understands k/m-suffixed word counts (100k, 1m), not raw
    // digits — format accordingly so the bar round-trips back to the same filter.
    const wc = (n: number) => (n % 1_000_000 === 0 ? `${n / 1_000_000}m` : `${Math.round(n / 1000)}k`)
    if (wordMin != null && wordMax != null) parts.push(`words:${wc(wordMin)}-${wc(wordMax)}`)
    else if (wordMin != null) parts.push(`words:>${wc(wordMin)}`)
    else if (wordMax != null) parts.push(`words:<${wc(wordMax)}`)
    if (language) parts.push(`lang:${q(language)}`)
    // author was missing, and this function REPLACES the bar's contents. So
    // clicking an author link searched correctly and then rewrote the bar
    // without it — leaving "rating:G rating:T ..." on screen and losing the
    // author the moment anything re-ran the search.
    if (authorFilter) parts.push(`author:${q(authorFilter)}`)
    return [freeText, ...parts].filter(Boolean).join(" ")
  }, [query, incFandoms, incShips, incChars, incTags, excFandoms, excShips,
      excChars, excTags, incRatings, status, wordMin, wordMax, language,
      // sections was missing, so the serializer closed over an empty list and
      // the search bar never mentioned a chosen section — the bar is meant to
      // be the single visible statement of what you asked for.
      authorFilter, explicit, sections])

  // Build search params
  // The Apply bar compares a signature of the current filters against the
  // signature of the last search, rather than tracking a boolean.
  //
  // A flag was the obvious approach and it oscillated: doSearch cleared it,
  // then the effect that watches the filters ran once more and set it straight
  // back, so the bar never went away. Comparing signatures cannot do that —
  // after a search the two are equal by construction, and they diverge only
  // when something the search depends on actually changes.
  const [appliedSig, setAppliedSig] = useState<string | null>(null)

  const buildParams = useCallback((pg: number, queryOverride?: string): SearchParams => {
    // queryOverride exists for the same reason explicitPage does, one function
    // down: React state is not readable in the tick you set it. Picking a recent
    // search called setQuery(q) and then doSearch(), and doSearch — a callback
    // captured in the render where query was still "" — searched for nothing.
    // That is the "first click does an empty search, second click works" bug.
    const pq = parseQuery(queryOverride ?? query)
    const merge = (sidebar: string[], parsed: string[]) =>
      [...new Set([...sidebar, ...parsed.filter(v => !sidebar.includes(v))])]

    return {
      sections: sections.length ? sections.join(",") : undefined,
      in_series: inSeries === "yes" ? true : inSeries === "no" ? false : undefined,
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
      dlp_min_rating:        dlpMinRating ?? undefined,
      word_count_min:        wordMin ?? pq.wordCountMin ?? undefined,
      word_count_max:        wordMax ?? pq.wordCountMax ?? undefined,
      updated_after:         updatedAfter || pq.updatedAfter || undefined,
      explicit,
      // Fall through to the parsed value like every other field does. The
      // sidebar has no author input, so a typed `author:` operator was the only
      // way to set it — and buildParams read the state variable alone, so the
      // operator parsed and was then dropped.
      author:                authorFilter || pq.author || undefined,
      match_mode:            matchMode,
      include_unknown:       includeUnknown || undefined,
      search_within:         searchWithin || undefined,
      sort,
      page:                  pg,
      per_page:              perPage,
    }
  }, [perPage, inSeries, query, sites, explicit, includeUnknown, authorFilter, matchMode, incFandoms, incChars, incShips, incTags, incRatings,
      incWarnings, incCats, excFandoms, excChars, excShips, excTags,
      status, crossovers, language, wordMin, wordMax, updatedAfter, searchWithin, sort,
      // sections was missing here, so buildParams closed over the empty array it
      // was created with: clicking a section updated the state, re-rendered the
      // pill as selected, and sent a search that still said nothing about it.
      // The control looked like it worked and changed no results.
      dlpMinRating, sections])

  // Everything a search depends on, in one string. Cheap to compare and it
  // cannot drift from the real dependency list the way a hand-maintained
  // boolean does.
  const filterSig = JSON.stringify([
    sites, incFandoms, incChars, incShips, incTags, incRatings, incWarnings,
    incCats, excFandoms, excChars, excShips, excTags, status, crossovers,
    language, wordMin, wordMax, updatedAfter, explicit, includeUnknown,
    authorFilter, matchMode, sort, dlpMinRating, sections, inSeries,
  ])
  const filtersDirty = appliedSig !== null && appliedSig !== filterSig


  const doSearch = useCallback(async (resetPage = true, explicitPage?: number,
                                      explicitQuery?: string) => {
    // explicitPage lets pagination pass the target page directly, avoiding the
    // stale-closure bug where setPage(p=>p+1) hadn't flushed before doSearch ran.
    hasSearchedRef.current = true   // filter changes start queueing from here on
    setAppliedSig(filterSig)        // this is now what the results reflect
    const pg = explicitPage ?? (resetPage ? 1 : page)
    if (resetPage) setPage(1)
    else if (explicitPage) setPage(explicitPage)
    const p = buildParams(pg, explicitQuery)

    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(p)) {
      if (v !== undefined && v !== null && v !== "") qs.set(k, String(v))
    }
    // Remember where to come back to. Recorded here rather than in an effect
    // watching the URL, because this is the one place a search is deliberately
    // performed — the URL also changes on remount and on Back, and neither is a
    // new search worth overwriting the memory with.
    rememberSearch(qs.toString())
    startTransition(() => router.push(`${pathname}?${qs.toString()}`, { scroll: false }))

    setLoading(true)
    setError(null)

    // Save to recent searches
    const effectiveQuery = explicitQuery ?? query
    if (effectiveQuery.trim()) {
      const recents = JSON.parse(localStorage.getItem("ficatlas:recent-searches") ?? "[]")
      const next = [effectiveQuery.trim(),
                    ...recents.filter((q: string) => q !== effectiveQuery.trim())].slice(0, 20)
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
        // If THAT fails too, the error thrown is the index-only one, which is
        // the honest thing to report — the live top-up is an extra, and blaming
        // it for an index outage would send someone looking in the wrong place.
        data = await searchStories({ ...p, live: false } as any)
      }
      setResults(data)
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
      const hasQuery = (pg === 1) && (effectiveQuery.trim().length > 0 || (p as any).fandoms)
      // Admin-gated server-side, so for a logged-out visitor this fired a 401
      // on every thin result set — and they saw no benefit either way, since
      // the deepened results only appear after the re-search it triggers.
      if (thin && hasQuery && sites.includes("ao3") && user?.can_manage
          && autoDeepenedRef.current !== query && !refreshing) {
        autoDeepenedRef.current = query
        refreshFromAO3()   // pulls 5 pages from AO3, persists, then re-searches
      }
    } catch (e: any) {
      // Classified rather than printed. "Failed to fetch" is what the browser
      // says when it cannot open a socket, and it told a reader nothing about
      // whether the fault was theirs, ours, or worth retrying.
      setError(describeError(e, e?.status))
    } finally {
      setLoading(false)
    }
  }, [buildParams, page, pathname, router, query, sites, refreshing, filterSig])

  // Editing the bar edits the filters.
  //
  // The comment below has claimed since it was written that the bar is "the
  // single visible source of truth", and only half of that was implemented: the
  // panel wrote to the bar, and nothing read it back. So deleting `author:X`
  // from the bar left authorFilter set, the next serialisation put the text
  // straight back, and buildParams searched on the state anyway — the filter you
  // just removed was still applied AND still displayed. Same for every other
  // token the panel owns.
  useEffect(() => {
    if (!barEditedRef.current) return
    barEditedRef.current = false
    const pq = parseQuery(query)
    const same = (a: string[], b: string[]) =>
      a.length === b.length && a.every((v, i) => v === b[i])
    const sync = (next: string[], cur: string[], set: (v: string[]) => void) => {
      if (!same(next, cur)) set(next)
    }
    sync(pq.fandoms, incFandoms, setIncFandoms)
    sync(pq.relationships, incShips, setIncShips)
    sync(pq.characters, incChars, setIncChars)
    sync(pq.tags, incTags, setIncTags)
    sync(pq.excFandoms, excFandoms, setExcFandoms)
    sync(pq.excRelationships, excShips, setExcShips)
    sync(pq.excCharacters, excChars, setExcChars)
    sync(pq.excTags, excTags, setExcTags)
    sync(pq.sections, sections, setSections)
    if ((pq.author ?? "") !== authorFilter) setAuthorFilter(pq.author ?? "")
    if ((pq.language ?? "") !== language) setLanguage(pq.language ?? "")
    const nextStatus = pq.status ? [pq.status] : []
    sync(nextStatus, status, setStatus)
    if ((pq.wordCountMin ?? undefined) !== wordMin) setWordMin(pq.wordCountMin ?? undefined)
    if ((pq.wordCountMax ?? undefined) !== wordMax) setWordMax(pq.wordCountMax ?? undefined)
    // Ratings only when the bar actually names some: an empty list is "the
    // default set", not "none selected", and treating it as the latter cleared
    // the rating pills every time you edited a word of free text.
    if (pq.ratings.length) sync(pq.ratings, incRatings, setIncRatings)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query])

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

    // Filters no longer fire a search on their own.
    //
    // The debounce was 350ms, which is shorter than the gap between two
    // deliberate clicks. Choosing a fandom, then a rating, then a section meant
    // three searches, the results jumping under you between each — and the one
    // you were actually building never ran until you stopped moving.
    //
    // The mobile drawer always had an explicit Apply and did not have this
    // problem; the desktop sidebar now works the same way. Changes accumulate,
    // a bar appears saying how many are waiting, and the search runs when you
    // say so. Enter in the search box still searches immediately.
    // no-op: the Apply bar derives its state from the signatures below
    return () => { if (filterDebounceRef.current) clearTimeout(filterDebounceRef.current) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sites, incFandoms, incChars, incShips, incTags, incRatings, incWarnings,
      incCats, excFandoms, excChars, excShips, excTags, status, crossovers,
      language, wordMin, wordMax, updatedAfter, explicit, includeUnknown, authorFilter,
      // sections belongs here too: this is the effect that re-runs the search
      // when a filter changes, so leaving it out meant a section click updated
      // the pill and then sat there.
      matchMode, sort, dlpMinRating, sections])

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
    { barEditedRef.current = true
      setQuery(q => q.replace(raw, "").replace(/\s+/g, " ").trim()) }

  const surpriseMe = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const API_BASE = ""
      // Use the current fandom filter if the user has one set, for relevant surprises.
      const params = new URLSearchParams({ count: "8" })
      if (incFandoms.length > 0) params.set("fandom", incFandoms[0])
      const r = await fetch(`${API_BASE}/api/search/random?${params.toString()}`)
      if (!r.ok) throw describeError(null, r.status)
      const cards = await r.json()
      setResults({
        total: cards.length, page: 1, per_page: cards.length,
        results: cards, sites_searched: [], live_count: 0,
      } as any)
      setParsedTokens([])
      window.scrollTo({ top: 0, behavior: "smooth" })
    } catch (e: any) {
      setError(e?.kind ? e : describeError(e))
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
    (dlpMinRating != null ? 1 : 0) +
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
              {/* Explains the sort that is CURRENTLY selected, not all nine.
                  The caveat worth reading differs per option — engagement
                  counts are missing for most of the index, word counts are not
                  — and a single paragraph cannot say that at the moment it
                  applies. */}
              <HelpTip label={`About the ${(SORT_OPTIONS.find(o => o.value === sort)?.label ?? "current").toLowerCase()} sort`}>
                <strong>{SORT_OPTIONS.find(o => o.value === sort)?.label}</strong>{" "}
                {SORT_OPTIONS.find(o => o.value === sort)?.help}
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

          {/* FictionAlley sections. Only shown when FicAlley is among the
              selected sites, because for an AO3-only search the control would
              filter nothing and just add noise to an already dense sidebar. */}
          {sites.includes("fictionalley") && (
            <FilterSection label="FictionAlley subsites" count={sections.length}
              defaultOpen={sections.length > 0}>
              <p className="filter-note">
                Five archives behind one banner.{" "}
                <HelpTip label="What are subsites?">{sectionHelpBody}</HelpTip>
              </p>
              <div className="pill-row">
                {FICALLEY_SECTIONS.map(sec => (
                  <button key={sec.value}
                    className={`pill ${sections.includes(sec.value) ? "pill--on" : ""}`}
                    title={sec.help}
                    onClick={() => setSections(
                      sections.includes(sec.value)
                        ? sections.filter(v => v !== sec.value)
                        : [...sections, sec.value])}>
                    {sec.label}
                  </button>
                ))}
              </div>
            </FilterSection>
          )}

          <div className="sidebar__group">
            <label className="sidebar__label">
              Picking several
              <HelpTip label="How picking more than one filter works">
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
                ? "Story must have all of them — this is how you find crossovers."
                : "Story needs any one of them — useful when a fandom has several tag spellings."}
            </p>
          </div>

          <div className="sidebar__group">
            <label className="sidebar__label">
              Series
              <HelpTip label="About series">
                <p>Some works are one part of a longer sequence. Filtering here
                is really two different searches: something to sink into for a
                month, or something you can finish tonight without acquiring a
                reading list.</p>
                <p className="helptip__aside">
                  AO3 publishes its series; FanFiction.net and FictionAlley have
                  no such field, so those are read from what authors wrote in
                  their titles and summaries.
                </p>
              </HelpTip>
            </label>
            <div className="pill-row">
              {[["", "Either"], ["yes", "In a series"], ["no", "Standalone"]].map(([v, label]) => (
                <button key={v} aria-pressed={inSeries === v}
                  className={`pill ${inSeries === v ? "pill--on" : ""}`}
                  onClick={() => setInSeries(v)}>{label}</button>
              ))}
            </div>
          </div>

          <div className="sidebar__group">
            <label className="checkbox-row">
              <input type="checkbox" checked={includeUnknown}
                onChange={e => setIncludeUnknown(e.target.checked)} />
              <span>Include stories with no ship or character data</span>
            </label>
            <HelpTip label="Stories with no ship or character data">
              <p>Filtering by a ship or a character normally returns only stories
              that actually list one, so a Drarry search is Drarry and not
              &ldquo;might be&rdquo;. This widens it to stories where we have no
              such data either way.</p>
              <p>How much that helps depends entirely on the archive:</p>
              <ul className="helptip__list">
                <li><strong>AO3</strong> — 59% list a ship and 65% a character,
                  so the filter already works well and this adds mostly noise.</li>
                <li><strong>FanFiction.net</strong> — 1.3% and 1.7%. FF.net does
                  not publish either as a field, so a ship filter finds almost
                  nothing there unless you tick this.</li>
                <li><strong>FictionAlley</strong> — 18% ships but 82% characters.</li>
              </ul>
              <p className="helptip__aside">
                Every story has freeform tags; this is only about ships and
                characters. More results, less certainty.
              </p>
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

          <FilterSection label="Archive Warnings" note={coverageWarning("warnings", sites)} highlighted={fromSearch("warnings").length > 0} count={incWarnings.length}>
            {AO3_WARNINGS.map(w => (
              <label key={w} className={`check-row ${fromSearch("warnings").includes(w) ? "check-row--lit" : ""}`}>
                <input type="checkbox" checked={incWarnings.includes(w)}
                  onChange={() => tog(incWarnings, setIncWarnings, w)} />
                <span>{w}</span>
              </label>
            ))}
          </FilterSection>

          <FilterSection label="Categories" note={coverageWarning("categories", sites)} highlighted={fromSearch("categories").length > 0} count={incCats.length}>
            <Pills options={CATEGORIES.map(c => ({ id: c, label: c }))}
              selected={incCats} onToggle={id => tog(incCats, setIncCats, id)}
              highlighted={fromSearch("categories")} />
          </FilterSection>

          <FilterSection label="Fandoms" highlighted={parsedLive.fandoms.length > 0} count={incFandoms.length}>
            <TagInput value={incFandoms} onChange={setIncFandoms}
              placeholder="e.g. Harry Potter" highlighted={parsedLive.fandoms} kind="fandom" />
          </FilterSection>

          <FilterSection label="Relationships" note={coverageWarning("relationships", sites)} highlighted={parsedLive.relationships.length > 0} count={incShips.length}>
            <TagInput value={incShips} onChange={setIncShips}
              placeholder="e.g. Draco/Hermione" highlighted={parsedLive.relationships} kind="relationship" />
          </FilterSection>

          <FilterSection label="Characters" note={coverageWarning("characters", sites)} highlighted={parsedLive.characters.length > 0} count={incChars.length}>
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
            <WordCountSlider
              min={wordMin ?? parsedLive.wordCountMin ?? undefined}
              max={wordMax ?? parsedLive.wordCountMax ?? undefined}
              onChange={(lo, hi) => { setWordMin(lo); setWordMax(hi) }} />
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

          <FilterSection label="DLP rating" highlighted={dlpMinRating != null}>
            <p className="filter-note">
              DarkLordPotter&rsquo;s curated list, rated by its readers. Picking a
              minimum also restricts results to that list.
            </p>
            <div className="pills">
              {[
                { label: "Any", value: undefined },
                { label: "3+", value: 3 },
                { label: "3.5+", value: 3.5 },
                { label: "4+", value: 4 },
                { label: "4.5+", value: 4.5 },
              ].map(o => (
                <button key={o.label}
                  onClick={() => setDlpMinRating(o.value)}
                  className={`pill ${dlpMinRating === o.value ? "pill--on" : ""}`}>
                  {o.label !== "Any" && <span className="pill__star">★</span>}{o.label}
                </button>
              ))}
            </div>
          </FilterSection>

          <FilterSection label="Language" highlighted={!!parsedLive.language}>
            {/* A dropdown, not free text. Stored values are a mix of English and
                native names ("Chinese" and "中文-普通话 國語" both occur), so a
                typed name only ever matched one spelling. Picking a canonical
                name lets the backend expand it across every spelling. */}
            <select className="select w-full"
              value={language || parsedLive.language || ""}
              onChange={e => setLanguage(e.target.value)}>
              <option value="">Any language</option>
              {LANGUAGE_OPTIONS.map(l => (
                <option key={l.value} value={l.value}>
                  {l.label} ({formatNumber(l.count)})
                </option>
              ))}
            </select>
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
          {/* Waiting-filters bar.
          Filters used to search on their own after 350ms, so picking three of
          them ran three searches and the results moved while you were still
          choosing. Now they queue and this says so. Sticky, because the sidebar
          is long and the control has to be reachable from wherever you are in
          it. */}
      {filtersDirty && (
        <div className="apply-bar" role="status">
          <span>Filters changed.</span>
          <button className="btn btn--primary apply-bar__go" onClick={() => doSearch()}>
            Apply
          </button>
          <kbd className="apply-bar__kbd">or press Enter</kbd>
        </div>
      )}

      {/* Search bar */}
          <div className="search-wrap">
            {/* Import needs admin (see StoryCard), and this banner promises to
                fetch and add the story — a promise we cannot keep for a visitor
                whose click would 401. They can still paste the URL and search. */}
            {/* Shown to everyone now, not only to operators. The backend has
                supported private reader imports since the My-shelf work, so
                gating the whole panel on can_import hid a feature readers have
                — and left the one visible copy claiming their import would be
                "fully searchable", which was never true of a private one. */}
            {detectedUrl && (
              <div className="url-detected">
                <span className="url-detected__icon">↓</span>
                <div className="url-detected__text">
                  <strong>{detectedUrl.site === "ao3" ? "AO3" : "FF.net"} story detected</strong>
                  {/* This used to say "fully searchable" to everyone, which was
                      wrong twice: a reader's import is private — deliberately
                      invisible to search, because it republishes nothing — and
                      the button sent private=false regardless, so it 403'd for
                      every reader who pressed it. */}
                  <span className="url-detected__sub">
                    {!user
                      ? "Sign in and we'll fetch the full text so you can read it here. Your copy stays yours — it is not added to the public index."
                      : user.can_manage
                      ? "Fetched via FicHub and added to the shared index — readable in-app and searchable by everyone."
                      : "Fetched via FicHub onto your own shelf — readable in-app, and visible only to you."}
                  </span>
                </div>
                {user ? (
                  <button onClick={importDetectedUrl} disabled={importing} className="btn btn--primary">
                    {importing ? "Importing…" : user.can_manage ? "Add to index" : "Add to my shelf"}
                  </button>
                ) : (
                  <Link href="/login" className="btn btn--primary">Sign in</Link>
                )}
              </div>
            )}
            {importMsg && <div className="alert alert--success" style={{marginBottom:8}}>{importMsg}</div>}
            <div className="search-bar">
              <div className="search-input-wrap">
                <input type="text" className="search-input"
                  placeholder={exampleQuery}
                  value={query}
                  onChange={e => { barEditedRef.current = true; setQuery(e.target.value) }}
                  onKeyDown={e => {
                    if (e.key === "Enter") { setSearchFocused(false); doSearch() }
                    if (e.key === "Escape") setSearchFocused(false)
                  }}
                  onFocus={() => setSearchFocused(true)}
                  onBlur={() => setSearchFocused(false)} />
                {query && (
                  <button className="search-clear" aria-label="Clear search text"
                    title="Clear the search box (keeps your filters)"
                    onClick={() => { barEditedRef.current = true; setQuery("") }}>✕</button>
                )}
                <SyntaxHelp onInsert={insertSyntax} />
                <RecentSearches
                  open={searchFocused && !query.trim()}
                  // The query goes to doSearch directly rather than via state:
                  // setQuery has not flushed yet when this runs, and waiting a
                  // tick would not help either, because doSearch is captured
                  // from this render.
                  onPick={q => {
                    setSearchFocused(false)
                    setQuery(q)
                    doSearch(true, undefined, q)
                  }}
                  onDismiss={() => setSearchFocused(false)} />
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

          {error && (
            <div className="alert alert--error" role="alert">
              <span>{error.message}</span>
              {error.retryable && (
                <button className="alert__retry" onClick={() => doSearch()}>
                  Try again
                </button>
              )}
            </div>
          )}

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

          {/* The way out.
              Sits above the results rather than in the header, because this is
              about the search you are looking at, not about the site — and it
              says what it will discard, since "Clear" with no object is the
              button people hesitate over. Only rendered when there is something
              to leave. */}
          {searchIsActive && (
            <div className="search-exit">
              <span className="search-exit__what">
                {query.trim() ? (
                  <>Searching <strong>{query.trim().length > 44
                    ? query.trim().slice(0, 44) + "…" : query.trim()}</strong></>
                ) : activeFilters > 0 ? (
                  <>Browsing with <strong>{activeFilters}</strong>{" "}
                    {activeFilters === 1 ? "filter" : "filters"}</>
                ) : (
                  <>Showing results</>
                )}
                {query.trim() && activeFilters > 0 && (
                  <> · <strong>{activeFilters}</strong>{" "}
                    {activeFilters === 1 ? "filter" : "filters"}</>
                )}
              </span>
              <button className="search-exit__btn" onClick={exitSearch}
                title="Clear the search and every filter, and start again (Esc)">
                <span aria-hidden="true">✕</span> Start over
              </button>
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

                  {/* Without this it looks like the filters are broken: results
                      appear that have no value for the field being filtered. */}
                  {includeUnknown && (
                    <button className="results-bar__loose"
                      onClick={() => setIncludeUnknown(false)}
                      title="Results include stories with no data for the fields you filtered on. Click to show only confirmed matches.">
                      · incl. untagged ✕
                    </button>
                  )}
                </span>
                <span className="results-bar__actions">
                  {sites.includes("ao3") && user?.can_manage && (
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
                          Include stories with no ship or character data
                        </button>
                      </>
                    ) : (
                      <p className="no-results__sub">
                        Try removing a filter, broadening the word count, or checking a different site.
                      </p>
                    )}
                    {sites.includes("ao3") && user?.can_manage && (
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

          {!results && !loading && <EmptyState onSurprise={surpriseMe} />}
        </main>
      </div>
    </div>
  )
}

// NOTE — every search currently runs TWICE, and this is deliberate for now.
//
// doSearch writes its parameters into the URL, SearchPageKeyed keys the
// component on that URL, so the component remounts and its mount effect runs
// the same search again. Wasteful on a 19.7M-row index.
//
// I tried to suppress the second run by remembering the last query string at
// module scope, and it broke the page twice: skipping the refetch left the
// remounted component with no results of its own (a facet click landed on a
// blank page), and caching the results alongside it raced — the marker is set
// before the fetch resolves, so the remount adopted stale results while the
// real ones arrived at a component that no longer existed.
//
// The correct fix is to stop navigate-and-remount being the mechanism that
// triggers a search at all, which is a restructure rather than a guard. Until
// then a duplicate query is much cheaper than an empty results page.

function SearchPageKeyed() {
  // Remount the search page whenever the URL's search params change.
  //
  // Clicking a tag, fandom, character or author is a Next.js <Link>, which is a
  // CLIENT-side navigation: the URL changes but the component does not remount.
  // Every piece of state here is initialised from the URL once (`get(...)` in
  // useState) and the run-on-mount search has an empty dependency array, so a
  // link updated the address bar and then nothing happened at all — no state
  // change, no search.
  //
  // Keying on the params is deliberate over syncing twenty state variables back
  // from the URL by hand: a link IS a new search, so starting the page over is
  // what should happen, and there is no partial-sync bug to get wrong.
  const params = useSearchParams()
  return <SearchPageInner key={params.toString()} />
}

export default function SearchPage() {
  return (
    <Suspense fallback={null}>
      <SearchPageKeyed />
    </Suspense>
  )
}
