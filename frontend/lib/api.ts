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

export const SITE_LABELS: Record<string, string> = {
  ao3: "AO3",
  ffnet: "FF.net",
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
