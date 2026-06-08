"use client"
import { useState, useCallback, useTransition } from "react"
import { useRouter, useSearchParams, usePathname } from "next/navigation"
import type { SearchParams, SearchResponse, StoryCard } from "@/lib/types"
import { searchStories, formatWordCount, formatNumber, chapterDisplay, SITE_LABELS, RATING_LABELS, SORT_OPTIONS, WORD_COUNT_PRESETS, DATE_PRESETS, AO3_WARNINGS, CATEGORIES } from "@/lib/api"

// ── Helpers ────────────────────────────────────────────────────────────────

function csv(s?: string): string[] {
  return s ? s.split(",").map(x => x.trim()).filter(Boolean) : []
}

function joinCsv(arr: string[]): string | undefined {
  return arr.length ? arr.join(",") : undefined
}

const SITE_OPTIONS = [
  { id: "ao3", label: "AO3" },
  { id: "ffnet", label: "FF.net" },
  { id: "wattpad", label: "Wattpad" },
]

const RATING_OPTIONS = [
  { id: "G", label: "General" },
  { id: "T", label: "Teen" },
  { id: "M", label: "Mature" },
  { id: "E", label: "Explicit" },
  { id: "NR", label: "Not Rated" },
]

// ── Tag list with truncation ───────────────────────────────────────────────

function TagList({ tags, className }: { tags: string[]; className?: string }) {
  const [expanded, setExpanded] = useState(false)
  const shown = expanded ? tags : tags.slice(0, 5)
  const extra = tags.length - 5
  return (
    <div className={`flex flex-wrap gap-1 ${className ?? ""}`}>
      {shown.map(t => (
        <span key={t} className="tag">{t}</span>
      ))}
      {!expanded && extra > 0 && (
        <button onClick={() => setExpanded(true)} className="tag tag--more">
          +{extra} more
        </button>
      )}
      {expanded && extra > 0 && (
        <button onClick={() => setExpanded(false)} className="tag tag--more">
          show less
        </button>
      )}
    </div>
  )
}

// ── Collapsible filter group ───────────────────────────────────────────────

function FilterGroup({ label, children, defaultOpen = false }: {
  label: string; children: React.ReactNode; defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="filter-group">
      <button className="filter-group__header" onClick={() => setOpen(o => !o)}>
        <span>{label}</span>
        <span className={`chevron ${open ? "chevron--open" : ""}`}>▶</span>
      </button>
      {open && <div className="filter-group__body">{children}</div>}
    </div>
  )
}

// ── Multi-tag input (include/exclude) ─────────────────────────────────────

function TagInput({ label, value, onChange, placeholder }: {
  label: string; value: string[]; onChange: (v: string[]) => void; placeholder?: string
}) {
  const [input, setInput] = useState("")
  const add = () => {
    const v = input.trim()
    if (v && !value.includes(v)) onChange([...value, v])
    setInput("")
  }
  return (
    <div className="tag-input">
      <label className="filter-label">{label}</label>
      <div className="tag-input__row">
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === "Enter" && (e.preventDefault(), add())}
          placeholder={placeholder ?? `Add ${label.toLowerCase()}…`}
          className="tag-input__field"
        />
        <button onClick={add} className="tag-input__add">+</button>
      </div>
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1">
          {value.map(v => (
            <button key={v} onClick={() => onChange(value.filter(x => x !== v))} className="tag tag--removable">
              {v} ✕
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Pill toggle ────────────────────────────────────────────────────────────

function PillGroup({ options, selected, onToggle }: {
  options: { id: string; label: string }[]
  selected: string[]
  onToggle: (id: string) => void
}) {
  return (
    <div className="flex flex-wrap gap-1">
      {options.map(o => (
        <button
          key={o.id}
          onClick={() => onToggle(o.id)}
          className={`pill ${selected.includes(o.id) ? "pill--active" : ""}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

// ── Result card ────────────────────────────────────────────────────────────

function ResultCard({ story }: { story: StoryCard }) {
  const siteClass = `badge badge--${story.site}`
  const ratingClass = story.rating ? `badge badge--rating-${story.rating.toLowerCase()}` : ""
  const isComplete = story.status === "complete"

  return (
    <article className="result-card">
      <div className="result-card__header">
        <div className="result-card__title-row">
          <a href={story.url} target="_blank" rel="noopener noreferrer" className="result-card__title">
            {story.title}
          </a>
          <div className="result-card__badges">
            <span className={siteClass}>{SITE_LABELS[story.site] ?? story.site}</span>
            {story.rating && <span className={ratingClass}>{RATING_LABELS[story.rating] ?? story.rating}</span>}
            {isComplete && <span className="badge badge--complete">✓ Complete</span>}
          </div>
        </div>
        <div className="result-card__author">
          {story.author_url
            ? <a href={story.author_url} target="_blank" rel="noopener noreferrer">{story.author}</a>
            : story.author}
          {story.fandoms.length > 0 && (
            <> · <span className="result-card__fandom">{story.fandoms.join(", ")}</span></>
          )}
        </div>
      </div>

      {story.summary && (
        <p className="result-card__summary">{story.summary}</p>
      )}

      <div className="result-card__stats">
        <span title="Word count">📄 {formatWordCount(story.word_count)} words</span>
        <span className="sep">·</span>
        <span title="Chapters">
          {chapterDisplay(story.chapter_count, story.chapter_count_total)} ch
        </span>
        {story.kudos > 0 && (<><span className="sep">·</span><span title="Kudos">♥ {formatNumber(story.kudos)}</span></>)}
        {story.hits > 0 && (<><span className="sep">·</span><span title="Hits">👁 {formatNumber(story.hits)}</span></>)}
        {story.comments > 0 && (<><span className="sep">·</span><span title="Comments">💬 {formatNumber(story.comments)}</span></>)}
        {story.language !== "English" && (<><span className="sep">·</span><span>{story.language}</span></>)}
        {story.updated_at && (
          <><span className="sep">·</span><span title="Updated">Updated {story.updated_at.split("T")[0]}</span></>
        )}
      </div>

      {/* Relationships first, then other tags */}
      {story.relationships.length > 0 && (
        <div className="result-card__rels">
          {story.relationships.map(r => (
            <span key={r} className="tag tag--rel">{r}</span>
          ))}
        </div>
      )}

      {story.tags.length > 0 && <TagList tags={story.tags} className="mt-1" />}
      {story.warnings.length > 0 && story.warnings.some(w => w !== "No Archive Warnings Apply") && (
        <div className="mt-1">
          {story.warnings.filter(w => w !== "No Archive Warnings Apply").map(w => (
            <span key={w} className="tag tag--warning">{w}</span>
          ))}
        </div>
      )}
    </article>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function SearchPage() {
  const router = useRouter()
  const pathname = usePathname()
  const rawParams = useSearchParams()
  const [isPending, startTransition] = useTransition()

  // Parse URL into state
  const get = (k: string) => rawParams.get(k) ?? undefined

  const [query, setQuery] = useState(get("q") ?? "")
  const [sites, setSites] = useState<string[]>(csv(get("sites") ?? "ao3,ffnet"))
  const [explicit, setExplicit] = useState(get("explicit") === "true")

  // Include filters
  const [incFandoms, setIncFandoms] = useState(csv(get("fandoms")))
  const [incCharacters, setIncCharacters] = useState(csv(get("characters")))
  const [incRelationships, setIncRelationships] = useState(csv(get("relationships")))
  const [incTags, setIncTags] = useState(csv(get("tags")))
  const [incRatings, setIncRatings] = useState(csv(get("ratings")))
  const [incWarnings, setIncWarnings] = useState(csv(get("warnings")))
  const [incCategories, setIncCategories] = useState(csv(get("categories")))

  // Exclude filters
  const [excFandoms, setExcFandoms] = useState(csv(get("exclude_fandoms")))
  const [excCharacters, setExcCharacters] = useState(csv(get("exclude_characters")))
  const [excRelationships, setExcRelationships] = useState(csv(get("exclude_relationships")))
  const [excTags, setExcTags] = useState(csv(get("exclude_tags")))
  const [excWarnings, setExcWarnings] = useState(csv(get("exclude_warnings")))

  // More options
  const [status, setStatus] = useState<string[]>(csv(get("status")))
  const [crossovers, setCrossovers] = useState(get("crossovers") ?? "include")
  const [language, setLanguage] = useState(get("language") ?? "")
  const [wordMin, setWordMin] = useState<number | undefined>(get("word_count_min") ? Number(get("word_count_min")) : undefined)
  const [wordMax, setWordMax] = useState<number | undefined>(get("word_count_max") ? Number(get("word_count_max")) : undefined)
  const [updatedAfter, setUpdatedAfter] = useState(get("updated_after") ?? "")
  const [searchWithin, setSearchWithin] = useState("")

  // Sort + pagination
  const [sort, setSort] = useState(get("sort") ?? "relevance")
  const [page, setPage] = useState(Number(get("page") ?? 1))

  // Results
  const [results, setResults] = useState<SearchResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const buildParams = (): SearchParams => ({
    q: query || undefined,
    sites: joinCsv(sites),
    fandoms: joinCsv(incFandoms),
    characters: joinCsv(incCharacters),
    relationships: joinCsv(incRelationships),
    tags: joinCsv(incTags),
    ratings: joinCsv(incRatings) ?? (explicit ? undefined : "G,T,M,NR"),
    warnings: joinCsv(incWarnings),
    categories: joinCsv(incCategories),
    crossovers: crossovers as any,
    exclude_fandoms: joinCsv(excFandoms),
    exclude_characters: joinCsv(excCharacters),
    exclude_relationships: joinCsv(excRelationships),
    exclude_tags: joinCsv(excTags),
    exclude_warnings: joinCsv(excWarnings),
    status: joinCsv(status),
    language: language || undefined,
    word_count_min: wordMin,
    word_count_max: wordMax,
    updated_after: updatedAfter || undefined,
    explicit,
    search_within: searchWithin || undefined,
    sort,
    page,
    per_page: 20,
  })

  const doSearch = useCallback(async (resetPage = true) => {
    const p = { ...buildParams(), page: resetPage ? 1 : page }
    if (resetPage) setPage(1)

    // Sync URL
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(p)) {
      if (v !== undefined && v !== null && v !== "") qs.set(k, String(v))
    }
    startTransition(() => router.push(`${pathname}?${qs.toString()}`, { scroll: false }))

    setLoading(true)
    setError(null)
    try {
      const data = await searchStories(p)
      setResults(data)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [buildParams, page, pathname, router])

  const toggleSite = (id: string) => setSites(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id])
  const toggleStatus = (id: string) => setStatus(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id])

  const wordPreset = (min?: number, max?: number) => { setWordMin(min); setWordMax(max) }

  return (
    <div className="layout">
      {/* Masthead */}
      <header className="masthead">
        <span className="wordmark">Fic<em>Atlas</em></span>
        <div className="masthead__right">
          <label className="explicit-toggle">
            <input type="checkbox" checked={explicit} onChange={e => setExplicit(e.target.checked)} />
            <span className="toggle-track"><span className="toggle-thumb" /></span>
            <span>Explicit content</span>
          </label>
        </div>
      </header>

      <div className="content">
        {/* Sidebar filters */}
        <aside className="sidebar">
          <div className="sidebar__section">
            <p className="sidebar__label">Sort by</p>
            <select value={sort} onChange={e => setSort(e.target.value)} className="select-full">
              {SORT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>

          <div className="sidebar__divider" />

          {/* Sites */}
          <div className="sidebar__section">
            <p className="sidebar__label">Sites</p>
            <PillGroup options={SITE_OPTIONS} selected={sites} onToggle={toggleSite} />
          </div>

          <div className="sidebar__divider" />

          <p className="sidebar__heading">Include</p>

          <FilterGroup label="Ratings" defaultOpen>
            <PillGroup
              options={RATING_OPTIONS.filter(r => explicit || r.id !== "E")}
              selected={incRatings}
              onToggle={id => setIncRatings(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id])}
            />
          </FilterGroup>

          <FilterGroup label="Warnings">
            {AO3_WARNINGS.map(w => (
              <label key={w} className="checkbox-row">
                <input type="checkbox" checked={incWarnings.includes(w)}
                  onChange={() => setIncWarnings(s => s.includes(w) ? s.filter(x => x !== w) : [...s, w])} />
                <span>{w}</span>
              </label>
            ))}
          </FilterGroup>

          <FilterGroup label="Categories">
            <PillGroup
              options={CATEGORIES.map(c => ({ id: c, label: c }))}
              selected={incCategories}
              onToggle={id => setIncCategories(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id])}
            />
          </FilterGroup>

          <FilterGroup label="Fandoms">
            <TagInput label="" value={incFandoms} onChange={setIncFandoms} placeholder="e.g. Harry Potter" />
          </FilterGroup>

          <FilterGroup label="Characters">
            <TagInput label="" value={incCharacters} onChange={setIncCharacters} placeholder="e.g. Hermione Granger" />
          </FilterGroup>

          <FilterGroup label="Relationships">
            <TagInput label="" value={incRelationships} onChange={setIncRelationships} placeholder="e.g. Draco/Hermione" />
          </FilterGroup>

          <FilterGroup label="Additional Tags">
            <TagInput label="" value={incTags} onChange={setIncTags} placeholder="e.g. slow burn" />
          </FilterGroup>

          <div className="sidebar__divider" />
          <p className="sidebar__heading">Exclude</p>

          <FilterGroup label="Ratings">
            <p className="filter-hint">Coming from exclude filters</p>
          </FilterGroup>

          <FilterGroup label="Warnings">
            {AO3_WARNINGS.map(w => (
              <label key={w} className="checkbox-row">
                <input type="checkbox" checked={excWarnings.includes(w)}
                  onChange={() => setExcWarnings(s => s.includes(w) ? s.filter(x => x !== w) : [...s, w])} />
                <span>{w}</span>
              </label>
            ))}
          </FilterGroup>

          <FilterGroup label="Fandoms">
            <TagInput label="" value={excFandoms} onChange={setExcFandoms} placeholder="Exclude fandom…" />
          </FilterGroup>

          <FilterGroup label="Characters">
            <TagInput label="" value={excCharacters} onChange={setExcCharacters} placeholder="Exclude character…" />
          </FilterGroup>

          <FilterGroup label="Relationships">
            <TagInput label="" value={excRelationships} onChange={setExcRelationships} placeholder="Exclude pairing…" />
          </FilterGroup>

          <FilterGroup label="Additional Tags">
            <TagInput label="" value={excTags} onChange={setExcTags} placeholder="Exclude tag…" />
          </FilterGroup>

          <div className="sidebar__divider" />
          <p className="sidebar__heading">More Options</p>

          <FilterGroup label="Crossovers">
            <div className="flex flex-col gap-1">
              {["include", "exclude", "only"].map(o => (
                <label key={o} className="checkbox-row">
                  <input type="radio" name="crossovers" value={o} checked={crossovers === o}
                    onChange={() => setCrossovers(o)} />
                  <span>{o.charAt(0).toUpperCase() + o.slice(1)}</span>
                </label>
              ))}
            </div>
          </FilterGroup>

          <FilterGroup label="Completion Status">
            <PillGroup
              options={[
                { id: "complete", label: "Complete" },
                { id: "in_progress", label: "In Progress" },
                { id: "abandoned", label: "Abandoned" },
              ]}
              selected={status}
              onToggle={toggleStatus}
            />
          </FilterGroup>

          <FilterGroup label="Word Count">
            <div className="flex flex-wrap gap-1 mb-2">
              {WORD_COUNT_PRESETS.map(p => (
                <button
                  key={p.label}
                  onClick={() => wordPreset(p.min, p.max)}
                  className={`pill ${wordMin === p.min && wordMax === p.max ? "pill--active" : ""}`}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <div className="flex gap-2">
              <input type="number" placeholder="Min" value={wordMin ?? ""} className="input-sm"
                onChange={e => setWordMin(e.target.value ? Number(e.target.value) : undefined)} />
              <input type="number" placeholder="Max" value={wordMax ?? ""} className="input-sm"
                onChange={e => setWordMax(e.target.value ? Number(e.target.value) : undefined)} />
            </div>
          </FilterGroup>

          <FilterGroup label="Date Updated">
            <div className="flex flex-col gap-1 mb-2">
              {DATE_PRESETS.map(p => (
                <button
                  key={p.label}
                  onClick={() => setUpdatedAfter(p.value ?? "")}
                  className={`pill ${updatedAfter === (p.value ?? "") ? "pill--active" : ""}`}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <input type="date" value={updatedAfter} className="input-sm w-full"
              onChange={e => setUpdatedAfter(e.target.value)} />
          </FilterGroup>

          <FilterGroup label="Language">
            <input type="text" placeholder="e.g. English, French…" value={language}
              onChange={e => setLanguage(e.target.value)} className="input-sm w-full" />
          </FilterGroup>

          <div className="sidebar__divider" />

          <div className="sidebar__section">
            <p className="sidebar__label">Search within results</p>
            <input type="text" placeholder="Narrow results…" value={searchWithin}
              onChange={e => setSearchWithin(e.target.value)} className="input-sm w-full" />
          </div>
        </aside>

        {/* Main content */}
        <main className="main">
          {/* Search bar */}
          <div className="search-bar">
            <input
              type="text"
              className="search-input"
              placeholder="Harry Potter Dramione slow burn completed >100k"
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === "Enter" && doSearch()}
            />
            <button className="search-btn" onClick={() => doSearch()} disabled={loading}>
              {loading ? "Searching…" : "Search"}
            </button>
          </div>

          {/* Results */}
          {error && <div className="error">{error}</div>}

          {results && (
            <>
              <div className="results-meta">
                <span>
                  <strong>{results.total.toLocaleString()}</strong> stories found
                  {results.sites_searched.length > 0 && ` across ${results.sites_searched.map(s => SITE_LABELS[s] ?? s).join(" & ")}`}
                </span>
                <span className="results-meta__right">
                  Page {results.page} of {Math.ceil(results.total / results.per_page)}
                </span>
              </div>

              <div className="result-list">
                {results.results.map(s => <ResultCard key={s.id} story={s} />)}
              </div>

              {/* Pagination */}
              <div className="pagination">
                <button disabled={page <= 1} onClick={() => { setPage(p => p - 1); doSearch(false) }} className="page-btn">
                  ← Prev
                </button>
                <span>Page {page} of {Math.ceil(results.total / results.per_page)}</span>
                <button
                  disabled={page >= Math.ceil(results.total / results.per_page)}
                  onClick={() => { setPage(p => p + 1); doSearch(false) }}
                  className="page-btn"
                >
                  Next →
                </button>
              </div>
            </>
          )}

          {!results && !loading && (
            <div className="empty-state">
              <p className="empty-state__title">Search across the fanfiction internet</p>
              <p className="empty-state__sub">AO3 · FF.net · and more to come</p>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
