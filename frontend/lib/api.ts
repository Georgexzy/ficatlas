import type { SearchParams, SearchResponse } from "./types"

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

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

export function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
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
