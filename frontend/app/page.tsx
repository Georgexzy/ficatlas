"use client"
import { useState, useCallback, useTransition, useEffect, useRef } from "react"
import { useRouter, useSearchParams, usePathname } from "next/navigation"
import type { SearchParams, SearchResponse, StoryCard } from "@/lib/types"
import { searchStories, formatWordCount, formatNumber, chapterDisplay, SITE_LABELS, RATING_LABELS, SORT_OPTIONS, WORD_COUNT_PRESETS, DATE_PRESETS, AO3_WARNINGS, CATEGORIES } from "@/lib/api"
import { parseQuery, parsedToSearchParams, type ParsedToken } from "@/lib/queryParser"
import IndexStatus from "./IndexStatus"
import SyntaxHelp from "./SyntaxHelp"

// ── Helpers ────────────────────────────────────────────────────────────────

function csv(s?: string): string[] {
  return s ? s.split(",").map(x => x.trim()).filter(Boolean) : []
}
function joinCsv(arr: string[]): string | undefined {
  return arr.length ? arr.join(",") : undefined
}

const SITE_OPTIONS = [
  { id: "ao3",    label: "AO3" },
  { id: "ffnet",  label: "FF.net" },
  { id: "wattpad",label: "Wattpad" },
]
const RATING_OPTIONS = [
  { id: "G", label: "General" }, { id: "T", label: "Teen" },
  { id: "M", label: "Mature" },  { id: "E", label: "Explicit" },
  { id: "NR", label: "Not Rated" },
]

// ── Tag list with truncation ───────────────────────────────────────────────

function TagList({ tags, className }: { tags: string[]; className?: string }) {
  const [expanded, setExpanded] = useState(false)
  const shown = expanded ? tags : tags.slice(0, 5)
  const extra = tags.length - 5
  return (
    <div className={`flex flex-wrap gap-1 ${className ?? ""}`}>
      {shown.map(t => <span key={t} className="tag">{t}</span>)}
      {!expanded && extra > 0 && <button onClick={() => setExpanded(true)} className="tag tag--more">+{extra} more</button>}
      {expanded  && extra > 0 && <button onClick={() => setExpanded(false)} className="tag tag--more">show less</button>}
    </div>
  )
}

// ── Collapsible filter group ───────────────────────────────────────────────

function FilterGroup({ label, children, defaultOpen = false, highlighted = false }: {
  label: string; children: React.ReactNode; defaultOpen?: boolean; highlighted?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen || highlighted)
  // Auto-open when highlighted from search bar
  useEffect(() => { if (highlighted) setOpen(true) }, [highlighted])
  return (
    <div className={`filter-group ${highlighted ? "filter-group--highlighted" : ""}`}>
      <button className="filter-group__header" onClick={() => setOpen(o => !o)}>
        <span>{label}{highlighted && <span className="filter-group__dot" />}</span>
        <span className={`chevron ${open ? "chevron--open" : ""}`}>▶</span>
      </button>
      {open && <div className="filter-group__body">{children}</div>}
    </div>
  )
}

// ── Tag input ──────────────────────────────────────────────────────────────

function TagInput({ value, onChange, placeholder, highlightedValues = [] }: {
  value: string[]; onChange: (v: string[]) => void; placeholder?: string; highlightedValues?: string[]
}) {
  const [input, setInput] = useState("")
  const add = () => {
    const v = input.trim()
    if (v && !value.includes(v)) onChange([...value, v])
    setInput("")
  }
  return (
    <div className="tag-input">
      <div className="tag-input__row">
        <input value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === "Enter" && (e.preventDefault(), add())}
          placeholder={placeholder} className="tag-input__field" />
        <button onClick={add} className="tag-input__add">+</button>
      </div>
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1">
          {value.map(v => (
            <button key={v} onClick={() => onChange(value.filter(x => x !== v))}
              className={`tag tag--removable ${highlightedValues.includes(v) ? "tag--from-search" : ""}`}>
              {highlightedValues.includes(v) && <span className="tag__search-indicator">⌕ </span>}
              {v} ✕
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Pill group ─────────────────────────────────────────────────────────────

function PillGroup({ options, selected, onToggle, highlightedIds = [] }: {
  options: { id: string; label: string }[]
  selected: string[]
  onToggle: (id: string) => void
  highlightedIds?: string[]
}) {
  return (
    <div className="flex flex-wrap gap-1">
      {options.map(o => (
        <button key={o.id} onClick={() => onToggle(o.id)}
          className={`pill ${selected.includes(o.id) ? "pill--active" : ""} ${highlightedIds.includes(o.id) ? "pill--from-search" : ""}`}>
          {highlightedIds.includes(o.id) && <span className="pill__indicator">⌕ </span>}
          {o.label}
        </button>
      ))}
    </div>
  )
}

// ── Result card ────────────────────────────────────────────────────────────

function ResultCard({ story }: { story: StoryCard }) {
  const isComplete = story.status === "complete"
  return (
    <article className={`result-card ${story.is_live ? "result-card--live" : ""}`}>
      {story.is_live && <span className="live-badge">Live</span>}
      <div className="result-card__header">
        <div className="result-card__title-row">
          <a href={story.url} target="_blank" rel="noopener noreferrer" className="result-card__title">
            {story.title}
          </a>
          <div className="result-card__badges">
            <span className={`badge badge--${story.site}`}>{SITE_LABELS[story.site] ?? story.site}</span>
            {story.rating && <span className={`badge badge--rating-${story.rating.toLowerCase()}`}>{RATING_LABELS[story.rating] ?? story.rating}</span>}
            {isComplete && <span className="badge badge--complete">✓ Complete</span>}
          </div>
        </div>
        <div className="result-card__author">
          {story.author_url
            ? <a href={story.author_url} target="_blank" rel="noopener noreferrer">{story.author}</a>
            : story.author}
          {story.fandoms.length > 0 && <> · <span className="result-card__fandom">{story.fandoms.join(", ")}</span></>}
        </div>
      </div>
      {story.summary && <p className="result-card__summary">{story.summary}</p>}
      <div className="result-card__stats">
        <span>📄 {formatWordCount(story.word_count)} words</span>
        <span className="sep">·</span>
        <span>{chapterDisplay(story.chapter_count, story.chapter_count_total)} ch</span>
        {story.kudos > 0 && <><span className="sep">·</span><span>♥ {formatNumber(story.kudos)}</span></>}
        {story.hits > 0 && <><span className="sep">·</span><span>👁 {formatNumber(story.hits)}</span></>}
        {story.comments > 0 && <><span className="sep">·</span><span>💬 {formatNumber(story.comments)}</span></>}
        {story.language !== "English" && <><span className="sep">·</span><span>{story.language}</span></>}
        {story.updated_at && <><span className="sep">·</span><span>Updated {story.updated_at.split("T")[0]}</span></>}
      </div>
      {story.relationships.length > 0 && (
        <div className="result-card__rels">
          {story.relationships.map(r => <span key={r} className="tag tag--rel">{r}</span>)}
        </div>
      )}
      {story.tags.length > 0 && <TagList tags={story.tags} className="mt-1" />}
      {story.warnings.filter(w => w !== "No Archive Warnings Apply").length > 0 && (
        <div className="mt-1">
          {story.warnings.filter(w => w !== "No Archive Warnings Apply").map(w =>
            <span key={w} className="tag tag--warning">{w}</span>)}
        </div>
      )}
    </article>
  )
}

// ── Parsed token pills (shown below search bar) ────────────────────────────

function TokenBar({ tokens, onRemove }: { tokens: ParsedToken[]; onRemove: (raw: string) => void }) {
  if (!tokens.length) return null
  const KEY_LABELS: Record<string, string> = {
    fandoms: "fandom", relationships: "ship", characters: "char",
    tags: "tag", ratings: "rating", status: "status", word_count: "words",
    updated_after: "since", language: "lang", sites: "site", crossovers: "crossover",
    warnings: "warning", categories: "category",
  }
  return (
    <div className="token-bar">
      {tokens.map((t, i) => (
        <button key={i} className={`token-pill ${t.exclude ? "token-pill--exclude" : ""}`}
          onClick={() => onRemove(t.raw)} title="Click to remove">
          <span className="token-pill__key">{t.exclude ? "−" : ""}{KEY_LABELS[t.key] ?? t.key}:</span>
          <span className="token-pill__val">{t.value}</span>
          <span className="token-pill__x">✕</span>
        </button>
      ))}
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function SearchPage() {
  const router = useRouter()
  const pathname = usePathname()
  const rawParams = useSearchParams()
  const [, startTransition] = useTransition()

  const get = (k: string) => rawParams.get(k) ?? undefined

  const [query, setQuery] = useState(get("q") ?? "")
  const [sites, setSites] = useState<string[]>(csv(get("sites") ?? "ao3,ffnet"))
  const [explicit, setExplicit] = useState(get("explicit") === "true")

  const [incFandoms,       setIncFandoms]       = useState(csv(get("fandoms")))
  const [incCharacters,    setIncCharacters]    = useState(csv(get("characters")))
  const [incRelationships, setIncRelationships] = useState(csv(get("relationships")))
  const [incTags,          setIncTags]          = useState(csv(get("tags")))
  const [incRatings,       setIncRatings]       = useState(csv(get("ratings")))
  const [incWarnings,      setIncWarnings]      = useState(csv(get("warnings")))
  const [incCategories,    setIncCategories]    = useState(csv(get("categories")))

  const [excFandoms,       setExcFandoms]       = useState(csv(get("exclude_fandoms")))
  const [excCharacters,    setExcCharacters]    = useState(csv(get("exclude_characters")))
  const [excRelationships, setExcRelationships] = useState(csv(get("exclude_relationships")))
  const [excTags,          setExcTags]          = useState(csv(get("exclude_tags")))
  const [excWarnings,      setExcWarnings]      = useState(csv(get("exclude_warnings")))

  const [status,       setStatus]       = useState<string[]>(csv(get("status")))
  const [crossovers,   setCrossovers]   = useState(get("crossovers") ?? "include")
  const [language,     setLanguage]     = useState(get("language") ?? "")
  const [wordMin,      setWordMin]      = useState<number | undefined>(get("word_count_min") ? Number(get("word_count_min")) : undefined)
  const [wordMax,      setWordMax]      = useState<number | undefined>(get("word_count_max") ? Number(get("word_count_max")) : undefined)
  const [updatedAfter, setUpdatedAfter] = useState(get("updated_after") ?? "")
  const [searchWithin, setSearchWithin] = useState("")
  const [sort,         setSort]         = useState(get("sort") ?? "relevance")
  const [page,         setPage]         = useState(Number(get("page") ?? 1))

  // Parsed tokens from search bar
  const [parsedTokens, setParsedTokens] = useState<ParsedToken[]>([])

  // Live parse as user types — for real-time sidebar highlighting
  const parsedLive = parseQuery(query)


  // Auto-populate sidebar from parsed query as user types
  useEffect(() => {
    const pq = parseQuery(query)
    if (pq.fandoms.length)          setIncFandoms(v => [...new Set([...v, ...pq.fandoms])])
    if (pq.relationships.length)    setIncRelationships(v => [...new Set([...v, ...pq.relationships])])
    if (pq.characters.length)       setIncCharacters(v => [...new Set([...v, ...pq.characters])])
    if (pq.tags.length)             setIncTags(v => [...new Set([...v, ...pq.tags])])
    if (pq.ratings.length)          setIncRatings(v => [...new Set([...v, ...pq.ratings])])
    if (pq.status)                  setStatus([pq.status])
    if (pq.wordCountMin != null)    setWordMin(pq.wordCountMin)
    if (pq.wordCountMax != null)    setWordMax(pq.wordCountMax)
    if (pq.updatedAfter)            setUpdatedAfter(pq.updatedAfter)
    if (pq.language)                setLanguage(pq.language)
    if (pq.excFandoms.length)       setExcFandoms(v => [...new Set([...v, ...pq.excFandoms])])
    if (pq.excRelationships.length) setExcRelationships(v => [...new Set([...v, ...pq.excRelationships])])
    if (pq.excCharacters.length)    setExcCharacters(v => [...new Set([...v, ...pq.excCharacters])])
    if (pq.excTags.length)          setExcTags(v => [...new Set([...v, ...pq.excTags])])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query])
  const [results, setResults] = useState<SearchResponse | null>(null)
  const [error,   setError]   = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [liveCount, setLiveCount] = useState(0)

  // ── Build params from all state ──────────────────────────────────────────

  const buildParams = useCallback((overridePage?: number): SearchParams => {
    // Merge live-parsed query filters with sidebar state
    const pq = parseQuery(query)
    const pp = parsedToSearchParams(pq)

    return {
      q:                     pq.cleanText || undefined,
      sites:                 joinCsv(sites),
      fandoms:               joinCsv([...incFandoms, ...pq.fandoms.filter(v => !incFandoms.includes(v))]),
      characters:            joinCsv([...incCharacters, ...pq.characters.filter(v => !incCharacters.includes(v))]),
      relationships:         joinCsv([...incRelationships, ...pq.relationships.filter(v => !incRelationships.includes(v))]),
      tags:                  joinCsv([...incTags, ...pq.tags.filter(v => !incTags.includes(v))]),
      ratings:               joinCsv(incRatings.length ? incRatings : pq.ratings) ?? (explicit ? undefined : "G,T,M,NR"),
      warnings:              joinCsv([...incWarnings, ...pq.warnings.filter(v => !incWarnings.includes(v))]),
      categories:            joinCsv(incCategories),
      crossovers:            (pq.crossovers ?? (crossovers !== "include" ? crossovers : undefined)) as any,
      exclude_fandoms:       joinCsv([...excFandoms,       ...pq.excFandoms]),
      exclude_characters:    joinCsv([...excCharacters,    ...pq.excCharacters]),
      exclude_relationships: joinCsv([...excRelationships, ...pq.excRelationships]),
      exclude_tags:          joinCsv([...excTags,          ...pq.excTags]),
      status:                status.length ? joinCsv(status) : (pq.status ?? undefined),
      language:              language || pq.language || undefined,
      word_count_min:        wordMin ?? pq.wordCountMin ?? undefined,
      word_count_max:        wordMax ?? pq.wordCountMax ?? undefined,
      updated_after:         updatedAfter || pq.updatedAfter || undefined,
      explicit,
      search_within:         searchWithin || undefined,
      sort,
      page:                  overridePage ?? page,
      per_page:              20,
    }
  }, [query, sites, explicit, incFandoms, incCharacters, incRelationships, incTags,
      incRatings, incWarnings, incCategories, excFandoms, excCharacters, excRelationships,
      excTags, excWarnings, status, crossovers, language, wordMin, wordMax,
      updatedAfter, searchWithin, sort, page])

  // ── Search ────────────────────────────────────────────────────────────────

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

  // ── Remove a token from the query bar ────────────────────────────────────

  const removeToken = (raw: string) => {
    setQuery(q => q.replace(raw, "").replace(/\s+/g, " ").trim())
  }

  // ── Sidebar highlight helpers ────────────────────────────────────────────

  const fromSearch = (key: string) => parsedLive.tokens.filter(t => t.key === key && !t.exclude).map(t => t.value)

  const toggleArr = (arr: string[], setArr: (v: string[]) => void, id: string) =>
    setArr(arr.includes(id) ? arr.filter(x => x !== id) : [...arr, id])

  return (
    <div className="layout">
      {/* Masthead */}
      <header className="masthead">
        <span className="wordmark">Fic<em>Atlas</em></span>
        <div className="masthead__right">
          <IndexStatus />
          <label className="explicit-toggle">
            <input type="checkbox" checked={explicit} onChange={e => setExplicit(e.target.checked)} />
            <span className="toggle-track"><span className="toggle-thumb" /></span>
            <span>Explicit content</span>
          </label>
        </div>
      </header>

      <div className="content">
        {/* Sidebar */}
        <aside className="sidebar">
          <div className="sidebar__section">
            <p className="sidebar__label">Sort by</p>
            <select value={sort} onChange={e => setSort(e.target.value)} className="select-full">
              {SORT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          <div className="sidebar__divider" />

          <div className="sidebar__section">
            <p className="sidebar__label">Sites</p>
            <PillGroup options={SITE_OPTIONS} selected={sites}
              onToggle={id => toggleArr(sites, setSites, id)}
              highlightedIds={fromSearch("sites")} />
          </div>
          <div className="sidebar__divider" />

          <p className="sidebar__heading">Include</p>

          <FilterGroup label="Ratings" defaultOpen highlighted={fromSearch("ratings").length > 0}>
            <PillGroup
              options={RATING_OPTIONS.filter(r => explicit || r.id !== "E")}
              selected={incRatings}
              onToggle={id => toggleArr(incRatings, setIncRatings, id)}
              highlightedIds={fromSearch("ratings")} />
          </FilterGroup>

          <FilterGroup label="Warnings" highlighted={fromSearch("warnings").length > 0}>
            {AO3_WARNINGS.map(w => (
              <label key={w} className={`checkbox-row ${fromSearch("warnings").includes(w) ? "checkbox-row--highlighted" : ""}`}>
                <input type="checkbox" checked={incWarnings.includes(w)}
                  onChange={() => toggleArr(incWarnings, setIncWarnings, w)} />
                <span>{w}</span>
              </label>
            ))}
          </FilterGroup>

          <FilterGroup label="Categories" highlighted={fromSearch("categories").length > 0}>
            <PillGroup
              options={CATEGORIES.map(c => ({ id: c, label: c }))}
              selected={incCategories}
              onToggle={id => toggleArr(incCategories, setIncCategories, id)}
              highlightedIds={fromSearch("categories")} />
          </FilterGroup>

          <FilterGroup label="Fandoms" highlighted={parsedLive.fandoms.length > 0}>
            <TagInput value={incFandoms} onChange={setIncFandoms}
              placeholder="e.g. Harry Potter"
              highlightedValues={parsedLive.fandoms} />
          </FilterGroup>

          <FilterGroup label="Characters" highlighted={parsedLive.characters.length > 0}>
            <TagInput value={incCharacters} onChange={setIncCharacters}
              placeholder="e.g. Hermione Granger"
              highlightedValues={parsedLive.characters} />
          </FilterGroup>

          <FilterGroup label="Relationships" highlighted={parsedLive.relationships.length > 0}>
            <TagInput value={incRelationships} onChange={setIncRelationships}
              placeholder="e.g. Draco/Hermione"
              highlightedValues={parsedLive.relationships} />
          </FilterGroup>

          <FilterGroup label="Additional Tags" highlighted={parsedLive.tags.length > 0}>
            <TagInput value={incTags} onChange={setIncTags}
              placeholder="e.g. slow burn"
              highlightedValues={parsedLive.tags} />
          </FilterGroup>

          <div className="sidebar__divider" />
          <p className="sidebar__heading">Exclude</p>

          <FilterGroup label="Fandoms" highlighted={parsedLive.excFandoms.length > 0}>
            <TagInput value={excFandoms} onChange={setExcFandoms} placeholder="Exclude fandom…"
              highlightedValues={parsedLive.excFandoms} />
          </FilterGroup>

          <FilterGroup label="Characters" highlighted={parsedLive.excCharacters.length > 0}>
            <TagInput value={excCharacters} onChange={setExcCharacters} placeholder="Exclude character…"
              highlightedValues={parsedLive.excCharacters} />
          </FilterGroup>

          <FilterGroup label="Relationships" highlighted={parsedLive.excRelationships.length > 0}>
            <TagInput value={excRelationships} onChange={setExcRelationships} placeholder="Exclude pairing…"
              highlightedValues={parsedLive.excRelationships} />
          </FilterGroup>

          <FilterGroup label="Additional Tags" highlighted={parsedLive.excTags.length > 0}>
            <TagInput value={excTags} onChange={setExcTags} placeholder="Exclude tag…"
              highlightedValues={parsedLive.excTags} />
          </FilterGroup>

          <div className="sidebar__divider" />
          <p className="sidebar__heading">More Options</p>

          <FilterGroup label="Crossovers">
            {["include","exclude","only"].map(o => (
              <label key={o} className="checkbox-row">
                <input type="radio" name="crossovers" value={o} checked={crossovers === o} onChange={() => setCrossovers(o)} />
                <span>{o.charAt(0).toUpperCase() + o.slice(1)}</span>
              </label>
            ))}
          </FilterGroup>

          <FilterGroup label="Completion Status" highlighted={!!parsedLive.status}>
            <PillGroup
              options={[
                { id: "complete", label: "Complete" },
                { id: "in_progress", label: "In Progress" },
                { id: "abandoned", label: "Abandoned" },
              ]}
              selected={status}
              onToggle={id => toggleArr(status, setStatus, id)}
              highlightedIds={parsedLive.status ? [parsedLive.status] : []} />
          </FilterGroup>

          <FilterGroup label="Word Count" highlighted={parsedLive.wordCountMin !== null || parsedLive.wordCountMax !== null}>
            <div className="flex flex-wrap gap-1 mb-2">
              {WORD_COUNT_PRESETS.map(p => (
                <button key={p.label}
                  onClick={() => { setWordMin(p.min); setWordMax(p.max) }}
                  className={`pill ${wordMin === p.min && wordMax === p.max ? "pill--active" : ""} ${parsedLive.wordCountMin === p.min && parsedLive.wordCountMax === p.max ? "pill--from-search" : ""}`}>
                  {p.label}
                </button>
              ))}
            </div>
            <div className="flex gap-2">
              <input type="number" placeholder="Min" value={wordMin ?? parsedLive.wordCountMin ?? ""} className="input-sm"
                onChange={e => setWordMin(e.target.value ? Number(e.target.value) : undefined)} />
              <input type="number" placeholder="Max" value={wordMax ?? parsedLive.wordCountMax ?? ""} className="input-sm"
                onChange={e => setWordMax(e.target.value ? Number(e.target.value) : undefined)} />
            </div>
            {(parsedLive.wordCountMin || parsedLive.wordCountMax) && (
              <p className="filter-hint filter-hint--from-search">
                ⌕ from search bar
              </p>
            )}
          </FilterGroup>

          <FilterGroup label="Date Updated" highlighted={!!parsedLive.updatedAfter}>
            <div className="flex flex-col gap-1 mb-2">
              {DATE_PRESETS.map(p => (
                <button key={p.label}
                  onClick={() => setUpdatedAfter(p.value ?? "")}
                  className={`pill ${updatedAfter === (p.value ?? "") ? "pill--active" : ""}`}>
                  {p.label}
                </button>
              ))}
            </div>
            <input type="date" value={updatedAfter || parsedLive.updatedAfter || ""} className="input-sm w-full"
              onChange={e => setUpdatedAfter(e.target.value)} />
            {parsedLive.updatedAfter && (
              <p className="filter-hint filter-hint--from-search">⌕ from search bar</p>
            )}
          </FilterGroup>

          <FilterGroup label="Language" highlighted={!!parsedLive.language}>
            <input type="text" placeholder="e.g. English, French…"
              value={language || parsedLive.language || ""}
              onChange={e => setLanguage(e.target.value)} className="input-sm w-full" />
          </FilterGroup>

          <div className="sidebar__divider" />
          <div className="sidebar__section">
            <p className="sidebar__label">Search within results</p>
            <input type="text" placeholder="Narrow results…" value={searchWithin}
              onChange={e => setSearchWithin(e.target.value)} className="input-sm w-full" />
          </div>
        </aside>

        {/* Main */}
        <main className="main">
          {/* Search bar */}
          <div className="search-bar">
            <div className="search-bar__input-wrap">
              <input type="text" className="search-input"
                placeholder='harry potter ship:Draco/Hermione >100k complete'
                value={query}
                onChange={e => setQuery(e.target.value)}
                onKeyDown={e => e.key === "Enter" && doSearch()} />
              <SyntaxHelp />
            </div>
            <button className="search-btn" onClick={() => doSearch()} disabled={loading}>
              {loading ? "Searching…" : "Search"}
            </button>
          </div>

          {/* Token bar */}
          {parsedTokens.length > 0 && (
            <TokenBar tokens={parsedTokens} onRemove={raw => {
              setQuery(q => q.replace(raw, "").replace(/\s+/g, " ").trim())
            }} />
          )}

          {error && <div className="error">{error}</div>}

          {results && (
            <>
              <div className="results-meta">
                <span>
                  <strong>{results.total.toLocaleString()}</strong> stories found
                  {results.sites_searched.length > 0 && ` across ${results.sites_searched.map(s => SITE_LABELS[s] ?? s).join(" & ")}`}
                  {liveCount > 0 && <span className="live-badge live-badge--meta"> +{liveCount} live</span>}
                </span>
                <span className="results-meta__right">Page {results.page} of {Math.ceil(results.total / results.per_page)}</span>
              </div>

              <div className="result-list">
                {results.results.map(s => <ResultCard key={s.id} story={s} />)}
              </div>

              <div className="pagination">
                <button disabled={page <= 1} onClick={() => { setPage(p => p - 1); doSearch(false) }} className="page-btn">← Prev</button>
                <span>Page {page} of {Math.ceil(results.total / results.per_page)}</span>
                <button disabled={page >= Math.ceil(results.total / results.per_page)}
                  onClick={() => { setPage(p => p + 1); doSearch(false) }} className="page-btn">Next →</button>
              </div>
            </>
          )}

          {!results && !loading && (
            <div className="empty-state">
              <p className="empty-state__title">Search across the fanfiction internet</p>
              <p className="empty-state__sub">AO3 · FF.net · and more to come</p>
              <p className="empty-state__hint">
                Try: <code>ship:Draco/Hermione &gt;100k complete</code> or <code>fandom:"My Hero Academia" wip updated:1y</code>
              </p>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
