"use client"
import { useState, useCallback, useTransition, useEffect } from "react"
import { useRouter, useSearchParams, usePathname } from "next/navigation"
import Link from "next/link"
import type { SearchParams, SearchResponse, StoryCard } from "@/lib/types"
import { searchStories, formatWordCount, formatNumber, chapterDisplay,
         SITE_LABELS, RATING_LABELS, SORT_OPTIONS, WORD_COUNT_PRESETS,
         DATE_PRESETS, AO3_WARNINGS, CATEGORIES } from "@/lib/api"
import { parseQuery, parsedToSearchParams, type ParsedToken } from "@/lib/queryParser"
import IndexStatus from "./IndexStatus"
import SyntaxHelp from "./SyntaxHelp"
import { useAuth } from "@/lib/auth"

// Header user menu - shows login/signup or username + logout
function UserMenu() {
  const { user, logout, loading } = useAuth()
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      const t = e.target as HTMLElement
      if (!t.closest(".user-menu")) setOpen(false)
    }
    document.addEventListener("click", close)
    return () => document.removeEventListener("click", close)
  }, [open])
  if (loading) return null
  if (!user) return <Link href="/login" className="header__link">Sign in</Link>
  return (
    <div className="user-menu">
      <button className="user-menu__btn" onClick={() => setOpen(o => !o)}>
        <span className="user-menu__avatar">{user.username.slice(0, 1).toUpperCase()}</span>
        <span className="user-menu__name">{user.username}</span>
      </button>
      {open && (
        <div className="user-menu__dropdown">
          <p className="user-menu__hint">Bookmarks &amp; progress sync to this account.</p>
          <button onClick={async () => { await logout(); setOpen(false) }}>Sign out</button>
        </div>
      )}
    </div>
  )
}

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
function TagInput({ value, onChange, placeholder, highlighted = [] }: {
  value: string[]; onChange: (v: string[]) => void; placeholder?: string; highlighted?: string[]
}) {
  const [input, setInput] = useState("")
  const add = (val?: string) => {
    const v = (val ?? input).trim()
    if (v && !value.includes(v)) onChange([...value, v])
    setInput("")
  }
  return (
    <div className="tag-input">
      <div className="tag-input__row">
        <input value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); add() } }}
          placeholder={placeholder} className="tag-input__field" />
        <button onClick={() => add()} className="tag-input__add" aria-label="Add">+</button>
      </div>
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
      const r = await fetch(`${API_BASE}/api/library/import-url`, { method: "POST", body: fd })
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

  // External link: FicAlley is defunct, link to Wayback. Snapshots were crawled with
  // the explicit :80 port in the URL, so we need to inject it for Wayback to match.
  const externalUrl = (() => {
    if (story.site !== "fictionalley") return story.url
    let u = story.url
    if (u.includes("fictionalley.org") && !u.includes("fictionalley.org:")) {
      u = u.replace("fictionalley.org/", "fictionalley.org:80/")
    }
    return `https://web.archive.org/web/2010/${u}`
  })()
  const externalLabel = story.site === "fictionalley"
    ? "Open on Wayback ↗"
    : `Open on ${SITE_LABELS[story.site] ?? story.site} ↗`

  // Can we one-click import? Only sites FicHub handles, and not already hosted.
  const canImport = !story.is_hosted && (story.site === "ao3" || story.site === "ffnet")

  return (
    <article className={`card ${story.is_live ? "card--live" : ""}`}>
      <div className="card__top">
        <div className="card__title-row">
          <Link href={`/story/${story.id}`} className="card__title">{story.title}</Link>
          <div className="card__badges">
            <span className={`badge badge--site-${story.site}`}>{SITE_LABELS[story.site] ?? story.site}</span>
            {story.rating && <span className={`badge badge--rating badge--${story.rating.toLowerCase()}`}>{RATING_LABELS[story.rating] ?? story.rating}</span>}
            {story.status === "complete" && <span className="badge badge--complete">Complete</span>}
            {story.tags?.includes("dlp_library") && <span className="badge badge--dlp" title="Curated by DarkLordPotter">DLP</span>}
            {story.is_live && <span className="badge badge--live">Live</span>}
          </div>
        </div>
        <p className="card__byline">
          {story.author_url
            ? <a href={story.author_url} target="_blank" rel="noopener noreferrer">{story.author}</a>
            : story.author}
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

      {story.summary && <p className="card__summary">{story.summary}</p>}

      <div className="card__meta">
        <span title="Words">📄 {formatWordCount(story.word_count)}</span>
        <span className="dot">·</span>
        <span title="Chapters">{chapterDisplay(story.chapter_count, story.chapter_count_total)} ch</span>
        {story.kudos > 0 && <><span className="dot">·</span><span title="Kudos">♥ {formatNumber(story.kudos)}</span></>}
        {story.hits > 0 && <><span className="dot">·</span><span title="Hits">👁 {formatNumber(story.hits)}</span></>}
        {story.comments > 0 && <><span className="dot">·</span><span title="Comments">💬 {formatNumber(story.comments)}</span></>}
        {story.language !== "English" && <><span className="dot">·</span><span>{story.language}</span></>}
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
function EmptyState({ onPick }: { onPick: (q: string) => void }) {
  const examples = [
    "ship:Draco/Hermione >100k complete",
    "fandom: Harry Potter marauders wip updated:2y",
    "fandom: Harry Potter -tag:fluff rating:M complete words:>50k",
  ]
  return (
    <div className="empty">
      <p className="empty__title">Search the fanfiction internet</p>
      <p className="empty__sub">AO3 · FF.net · FicAlley · and more</p>
      <div className="empty__examples">
        <p className="empty__examples-label">Try:</p>
        {examples.map(ex => (
          <button key={ex} className="empty__ex" onClick={() => onPick(ex)}>{ex}</button>
        ))}
      </div>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────
export default function SearchPage() {
  const router     = useRouter()
  const pathname   = usePathname()
  const rawParams  = useSearchParams()
  const [, startTransition] = useTransition()

  const get = (k: string) => rawParams.get(k) ?? undefined

  // Search bar
  const [query,   setQuery]   = useState(get("q") ?? "")
  const [sites,   setSites]   = useState<string[]>(csv(get("sites") ?? "ao3,ffnet,fictionalley"))
  const [explicit, setExplicit] = useState(get("explicit") === "true")

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
      search_within:         searchWithin || undefined,
      sort,
      page:                  pg,
      per_page:              20,
    }
  }, [query, sites, explicit, incFandoms, incChars, incShips, incTags, incRatings,
      incWarnings, incCats, excFandoms, excChars, excShips, excTags,
      status, crossovers, language, wordMin, wordMax, updatedAfter, searchWithin, sort])

  const doSearch = useCallback(async (resetPage = true) => {
    const pg = resetPage ? 1 : page
    if (resetPage) setPage(1)
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
      const data = await searchStories({ ...p, live: true } as any)
      setResults(data)
      setLiveCount((data as any).live_count ?? 0)
      setParsedTokens((data as any).parsed_tokens ?? [])
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [buildParams, page, pathname, router])

  const removeToken = (raw: string) =>
    setQuery(q => q.replace(raw, "").replace(/\s+/g, " ").trim())

  const totalPages = results ? Math.ceil(results.total / results.per_page) : 0

  return (
    <div className="shell">
      {/* ── Header ── */}
      <header className="header">
        <span className="logo">Fic<em>Atlas</em></span>
        <div className="header__right">
          <Link href="/library" className="header__link">Library</Link>
          <Link href="/settings" className="header__link">Settings</Link>
          <UserMenu />
          <IndexStatus />
          <label className="toggle-label">
            <input type="checkbox" checked={explicit} onChange={e => setExplicit(e.target.checked)} className="sr-only" />
            <span className="toggle-track"><span className="toggle-thumb" /></span>
            <span>Explicit</span>
          </label>
        </div>
      </header>

      <div className="layout">
        {/* ── Sidebar ── */}
        <aside className="sidebar">
          <div className="sidebar__top">
            <label className="sidebar__label">Sort</label>
            <select value={sort} onChange={e => setSort(e.target.value)} className="select">
              {SORT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>

          <div className="sidebar__group">
            <label className="sidebar__label">Sites</label>
            <Pills options={SITE_OPTIONS} selected={sites}
              onToggle={id => tog(sites, setSites, id)}
              highlighted={fromSearch("sites")} />
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
              placeholder="e.g. Harry Potter" highlighted={parsedLive.fandoms} />
          </FilterSection>

          <FilterSection label="Relationships" highlighted={parsedLive.relationships.length > 0} count={incShips.length}>
            <TagInput value={incShips} onChange={setIncShips}
              placeholder="e.g. Draco/Hermione" highlighted={parsedLive.relationships} />
          </FilterSection>

          <FilterSection label="Characters" highlighted={parsedLive.characters.length > 0} count={incChars.length}>
            <TagInput value={incChars} onChange={setIncChars}
              placeholder="e.g. Hermione Granger" highlighted={parsedLive.characters} />
          </FilterSection>

          <FilterSection label="Additional Tags" highlighted={parsedLive.tags.length > 0} count={incTags.length}>
            <TagInput value={incTags} onChange={setIncTags}
              placeholder="e.g. slow burn" highlighted={parsedLive.tags} />
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
              placeholder="Exclude fandom…" highlighted={parsedLive.excFandoms} />
          </FilterSection>

          <FilterSection label="Relationships" highlighted={parsedLive.excRelationships.length > 0} count={excShips.length}>
            <TagInput value={excShips} onChange={setExcShips}
              placeholder="Exclude pairing…" highlighted={parsedLive.excRelationships} />
          </FilterSection>

          <FilterSection label="Characters" highlighted={parsedLive.excCharacters.length > 0} count={excChars.length}>
            <TagInput value={excChars} onChange={setExcChars}
              placeholder="Exclude character…" highlighted={parsedLive.excCharacters} />
          </FilterSection>

          <FilterSection label="Additional Tags" highlighted={parsedLive.excTags.length > 0} count={excTags.length}>
            <TagInput value={excTags} onChange={setExcTags}
              placeholder="Exclude tag…" highlighted={parsedLive.excTags} />
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
        </aside>

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
                <SyntaxHelp />
              </div>
              <button className="search-btn" onClick={() => doSearch()} disabled={loading}>
                {loading ? <span className="search-btn__spinner" /> : "Search"}
              </button>
            </div>
            <TokenStrip tokens={parsedTokens} onRemove={raw => {
              setQuery(q => q.replace(raw, "").replace(/\s+/g, " ").trim())
            }} />
          </div>

          {error && <div className="alert alert--error">{error}</div>}

          {results && (
            <>
              <div className="results-bar">
                <span className="results-bar__count">
                  <strong>{results.total.toLocaleString()}</strong> stories
                  {results.sites_searched.length > 0 && ` · ${results.sites_searched.map(s => SITE_LABELS[s] ?? s).join(" + ")}`}
                  {liveCount > 0 && <span className="results-bar__live"> +{liveCount} live</span>}
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
                    <p className="no-results__sub">
                      Try removing a filter, broadening the word count, or checking a different site.
                      {sites.includes("ao3") && " You can also pull fresh AO3 results with Refresh above."}
                    </p>
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
                    onClick={() => { setPage(p => p - 1); doSearch(false) }}
                    className="page-btn">← Previous</button>
                  <span className="pagination__info">Page {page} of {totalPages}</span>
                  <button disabled={page >= totalPages}
                    onClick={() => { setPage(p => p + 1); doSearch(false) }}
                    className="page-btn">Next →</button>
                </div>
              )}
            </>
          )}

          {!results && !loading && <EmptyState onPick={(q) => { setQuery(q); setTimeout(() => doSearch(), 0) }} />}
        </main>
      </div>
    </div>
  )
}
