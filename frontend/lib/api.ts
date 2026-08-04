import type { SearchParams, SearchResponse } from "./types"

const API_BASE = ""  // relative — handled by Next.js rewrite to backend

export async function searchStories(params: SearchParams): Promise<SearchResponse> {
  const qs = new URLSearchParams()

  for (const [key, val] of Object.entries(params)) {
    if (val === undefined || val === null || val === "") continue
    qs.set(key, String(val))
  }

  const res = await fetch(`${API_BASE}/api/search?${qs.toString()}`, {
    next: { revalidate: 60 },
  })

  if (!res.ok) {
    const body = await res.text()
    throw new Error(`Search failed: ${res.status} ${body}`)
  }

  return res.json()
}

export function buildSearchParams(raw: Record<string, string | string[] | undefined>): SearchParams {
  const get = (k: string) => (Array.isArray(raw[k]) ? (raw[k] as string[])[0] : raw[k])
  const num = (k: string) => { const v = get(k); return v ? Number(v) : undefined }
  const bool = (k: string) => get(k) === "true"

  return {
    q: get("q"),
    sites: get("sites"),
    fandoms: get("fandoms"),
    characters: get("characters"),
    relationships: get("relationships"),
    tags: get("tags"),
    ratings: get("ratings"),
    warnings: get("warnings"),
    categories: get("categories"),
    crossovers: get("crossovers") as any,
    exclude_fandoms: get("exclude_fandoms"),
    exclude_characters: get("exclude_characters"),
    exclude_relationships: get("exclude_relationships"),
    exclude_tags: get("exclude_tags"),
    exclude_ratings: get("exclude_ratings"),
    exclude_warnings: get("exclude_warnings"),
    exclude_categories: get("exclude_categories"),
    status: get("status"),
    language: get("language"),
    word_count_min: num("word_count_min"),
    word_count_max: num("word_count_max"),
    updated_after: get("updated_after"),
    updated_before: get("updated_before"),
    published_after: get("published_after"),
    explicit: bool("explicit"),
    author: get("author"),
    match_mode: (get("match_mode") as "all" | "any") ?? "all",
    include_unknown: bool("include_unknown"),
    search_within: get("search_within"),
    sort: get("sort") ?? "relevance",
    page: num("page") ?? 1,
    per_page: num("per_page") ?? 20,
  }
}

export function formatWordCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`
  return String(n)
}

// Compact counts. Must go past millions: the index holds 126 BILLION words, and
// a millions-only formatter rendered that as "126305.5M".
export function formatNumber(n: number): string {
  if (!Number.isFinite(n)) return "—"
  const abs = Math.abs(n)
  if (abs >= 1_000_000_000_000) return `${(n / 1_000_000_000_000).toFixed(1)}T`
  if (abs >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

export function chapterDisplay(count: number, total?: number | null): string {
  if (total) return `${count}/${total}`
  return `${count}/?`
}

// The one place site names are spelled. Three copies of this existed — here and
// in IndexStatus and the story page — and they drifted: the two components had
// `fictionalley`, this one did not, so the results bar rendered the raw key and
// read "AO3 + FF.net + fictionalley" next to two properly branded names.
//
// Only ao3, ffnet and fictionalley appear in the data today; the rest are
// crawlers that exist but have imported nothing yet.
export const SITE_LABELS: Record<string, string> = {
  ao3: "AO3",
  ffnet: "FF.net",
  fictionalley: "FicAlley",
  hpffa: "HPFFA",
  hexfiles: "HexFiles",
  squidgeworld: "SquidgeWorld",
  wattpad: "Wattpad",
  royalroad: "Royal Road",
  spacebattles: "SpaceBattles",
}

export const RATING_LABELS: Record<string, string> = {
  G: "General",
  T: "Teen",
  M: "Mature",
  E: "Explicit",
  NR: "Not Rated",
}

export const SORT_OPTIONS = [
  { value: "relevance", label: "Relevance" },
  { value: "updated_desc", label: "Recently updated" },
  { value: "published_desc", label: "Newest" },
  { value: "kudos_desc", label: "Most kudos" },
  { value: "hits_desc", label: "Most hits" },
  { value: "bookmarks_desc", label: "Most bookmarks" },
  { value: "comments_desc", label: "Most comments" },
  { value: "word_count_desc", label: "Longest" },
  { value: "word_count_asc", label: "Shortest" },
]

export const AO3_WARNINGS = [
  "Creator Chose Not To Use Archive Warnings",
  "No Archive Warnings Apply",
  "Graphic Depictions Of Violence",
  "Major Character Death",
  "Rape/Non-Con",
  "Underage",
]

export const CATEGORIES = ["F/F", "F/M", "Gen", "M/M", "Multi", "Other"]

export const WORD_COUNT_PRESETS = [
  { label: "Any", min: undefined, max: undefined },
  { label: "< 1k",   min: undefined, max: 1_000 },
  { label: "1k–10k", min: 1_000,  max: 10_000 },
  { label: "10k–50k",min: 10_000, max: 50_000 },
  { label: "50k–100k",min: 50_000, max: 100_000 },
  { label: "> 100k", min: 100_000, max: undefined },
  { label: "> 200k", min: 200_000, max: undefined },
]

export const DATE_PRESETS = [
  { label: "Any time", value: undefined },
  { label: "Past week",  value: daysAgo(7) },
  { label: "Past month", value: daysAgo(30) },
  { label: "Past year",  value: daysAgo(365) },
]

function daysAgo(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString().split("T")[0]
}

// Languages offered in the filter dropdown, ordered by how much of the index
// each actually holds — counts measured, not guessed.
//
// The VALUE is the canonical English name. The backend expands it through
// language_aliases.py, so picking "Chinese" matches the 539,217 works stored as
// "中文-普通话 國語" as well as those stored as "Chinese". Typing the name by
// hand only ever matched one spelling, which is why the language filter used to
// return almost nothing.
export const LANGUAGE_OPTIONS: { value: string; label: string; count: number }[] = [
  { value: "English", label: "English", count: 17823095 },
  { value: "Chinese", label: "Chinese", count: 546782 },
  { value: "Spanish", label: "Spanish", count: 455861 },
  { value: "Russian", label: "Russian", count: 201735 },
  { value: "French", label: "French", count: 199461 },
  { value: "Indonesian", label: "Indonesian", count: 135172 },
  { value: "Portuguese", label: "Portuguese", count: 104543 },
  { value: "German", label: "German", count: 49738 },
  { value: "Italian", label: "Italian", count: 28961 },
  { value: "Polish", label: "Polish", count: 23481 },
  { value: "Ukrainian", label: "Ukrainian", count: 19186 },
  { value: "Filipino", label: "Filipino", count: 10050 },
  { value: "Vietnamese", label: "Vietnamese", count: 9488 },
  { value: "Czech", label: "Czech", count: 5678 },
  { value: "Hungarian", label: "Hungarian", count: 3999 },
  { value: "Korean", label: "Korean", count: 3466 },
  { value: "Turkish", label: "Turkish", count: 3374 },
  { value: "Japanese", label: "Japanese", count: 3139 },
  { value: "Swedish", label: "Swedish", count: 3130 },
  { value: "Finnish", label: "Finnish", count: 2773 },
  { value: "Dutch", label: "Dutch", count: 2640 },
  { value: "Thai", label: "Thai", count: 2086 },
  { value: "Norwegian", label: "Norwegian", count: 1270 },
  { value: "Danish", label: "Danish", count: 1061 },
  { value: "Belarusian", label: "Belarusian", count: 737 },
  { value: "Hebrew", label: "Hebrew", count: 611 },
  { value: "Esperanto", label: "Esperanto", count: 575 },
  { value: "Greek", label: "Greek", count: 503 },
  { value: "Latin", label: "Latin", count: 443 },
  { value: "Catalan", label: "Catalan", count: 383 },
  { value: "Romanian", label: "Romanian", count: 355 },
  { value: "Arabic", label: "Arabic", count: 352 },
  { value: "Persian", label: "Persian", count: 324 },
  { value: "Croatian", label: "Croatian", count: 229 },
  { value: "Hindi", label: "Hindi", count: 217 },
  { value: "Bulgarian", label: "Bulgarian", count: 196 },
  { value: "Serbian", label: "Serbian", count: 99 },
]

// Dates on a result card.
//
// The card used to print a bare "2026-01-11" with no label, which does not say
// whether that is when the story appeared or when it last changed — and those
// mean very different things when you are deciding whether to start a WIP. It
// also never showed published_at at all, so a work with no update date showed
// no date whatsoever even when we knew exactly when it was posted.
//
// Recent dates read better relative ("3 days ago" carries more than the date
// itself); anything older gets the actual date, because "412 days ago" does
// not.
export function formatStoryDate(iso?: string | null): string | null {
  if (!iso) return null
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return null
  const days = Math.floor((Date.now() - then.getTime()) / 86_400_000)
  if (days < 0) return then.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })
  if (days === 0) return "today"
  if (days === 1) return "yesterday"
  if (days < 30) return `${days} days ago`
  if (days < 60) return "last month"
  if (days < 365) return `${Math.round(days / 30)} months ago`
  return then.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })
}


// ── Shared index totals ─────────────────────────────────────────────────────
//
// Two components want this number — the header pill and the landing hero — and
// a page load was making FOUR requests for it. Each call site fires twice,
// because React re-invokes effects and the search page is deliberately keyed on
// its URL params so it remounts when a facet link is clicked.
//
// Chasing those remounts would be the wrong fix: the remount is intentional and
// the double-invoke is by design. What is wrong is that two callers wanting the
// same immutable-for-five-minutes number produce two requests. So the promise is
// shared: whoever asks first starts the fetch, everyone else awaits the same one,
// and the resolved value is reused until it goes stale.
//
// The server already caches this (it is a whole-table aggregate over 19.7M rows),
// so the cost was small — but it was four round trips on every single page view,
// and it made the network panel misleading when diagnosing anything else.
export interface IndexTotals {
  stories: number
  hosted: number
  total_words: number
  dlp?: number
  hpffa?: number
  indexed_last_hour?: number
  indexed_last_day?: number
}

const TOTALS_TTL_MS = 5 * 60 * 1000   // matches the server-side cache window

// The cache hangs off `window` rather than module scope. Module scope would in
// fact have been sufficient — I moved it here on a theory about code splitting
// that turned out to be wrong, and the note is kept because the measurement that
// disproved it is the useful part:
//
//   before dedup            4 fetch() calls, 4 network events
//   after dedup             1 fetch() call,  2 network events
//
// The stubborn "2" was not a second request. It is the service worker passing
// the request through to the network, which DevTools and Playwright both count
// as an event of its own. Counting network events was measuring the wrong
// thing; wrapping window.fetch and counting actual calls settled it.
//
// window scope is kept anyway: it is no more complex and it survives any future
// bundling change, whereas module scope quietly would not.
interface TotalsCache {
  value: IndexTotals | null
  at: number
  inflight: Promise<IndexTotals | null> | null
}

function cache(): TotalsCache {
  const w = globalThis as any
  if (!w.__ficatlasTotals) w.__ficatlasTotals = { value: null, at: 0, inflight: null }
  return w.__ficatlasTotals as TotalsCache
}

export function getIndexTotals(): Promise<IndexTotals | null> {
  const c = cache()
  if (c.value && Date.now() - c.at < TOTALS_TTL_MS) return Promise.resolve(c.value)
  if (c.inflight) return c.inflight        // someone else is already asking

  c.inflight = fetch("/api/stats/totals")
    .then(r => (r.ok ? r.json() : null))
    .then((d: IndexTotals | null) => {
      if (d) { c.value = d; c.at = Date.now() }
      return c.value
    })
    .catch(() => c.value)                  // keep the last good value on failure
    .finally(() => { c.inflight = null })

  return c.inflight
}
