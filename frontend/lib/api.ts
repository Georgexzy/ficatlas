import type { SearchParams, SearchResponse } from "./types"

const API_BASE = ""  // relative — handled by Next.js rewrite to backend

// How long to wait for a search before giving up on it.
//
// There was no limit at all, which is the worst thing a search box can do when
// the index is unwell: fetch() without a signal waits on the browser's own
// network timeout, which is minutes. A backend that was restarting, saturated or
// wedged did not produce an error — it produced a spinner that ran until the
// reader gave up and reloaded, and reloading fires the same request again. That
// is how a struggling index turns into a hammered one.
//
// The ceiling is the backend's own statement_timeout (60s in db/session.py) plus
// a little, so an honestly-slow query still gets its answer and only a genuinely
// unresponsive server trips this.
const SEARCH_TIMEOUT_MS = 65_000

import { loadMutes, withMutes } from "./mutelist"

export async function searchStories(
  params: SearchParams,
  opts?: { signal?: AbortSignal },
): Promise<SearchResponse> {
  const qs = new URLSearchParams()

  for (const [key, val] of Object.entries(params)) {
    if (val === undefined || val === null || val === "") continue
    qs.set(key, String(val))
  }

  // The reader's standing "never show me" list, merged into every search.
  //
  // Applied HERE rather than in the page's filter state, for two reasons:
  //
  //   * this is the one place every search passes through, so a mute cannot be
  //     forgotten by a code path that builds its parameters differently;
  //   * it keeps the mute list out of the URL. The address bar stays a clean,
  //     shareable description of the search, and a link sent to a friend does
  //     not carry a list of the ships and authors you refuse to read — which is
  //     private in a way the rest of a query simply is not.
  withMutes(qs, loadMutes())

  // The caller's signal (a superseded search) and our timeout both have to be
  // able to abort this one request.
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(new DOMException("timeout", "TimeoutError")),
                           SEARCH_TIMEOUT_MS)
  const relay = () => ctl.abort()
  opts?.signal?.addEventListener("abort", relay)

  try {
    const res = await fetch(`${API_BASE}/api/search?${qs.toString()}`, { signal: ctl.signal })

    if (!res.ok) {
      const body = await res.text()
      // Carries the status so the caller can say WHICH failure this was. It used
      // to throw a bare Error, so a 500 and an unreachable server produced the
      // same "something went wrong" — and the reader could not tell whether to
      // retry, narrow the search, or check their connection.
      const err: any = new Error(`Search failed: ${res.status} ${body.slice(0, 200)}`)
      err.status = res.status
      throw err
    }

    return await res.json()
  } finally {
    clearTimeout(timer)
    opts?.signal?.removeEventListener("abort", relay)
  }
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

// Each sort carries its own explanation rather than one paragraph covering all
// nine. Generic help gets ignored — the note that matters here is which sorts
// are unreliable on THIS index, and that differs per option: engagement counts
// are absent from the bulk dumps for the overwhelming majority of works, so
// "Most kudos" quietly means "most kudos among the ~0.01% that have any". A
// single blob of text cannot say that at the moment it is relevant.
export const SORT_OPTIONS = [
  { value: "relevance", label: "Relevance",
    help: "Ranks by how well a story matches the words you typed — title, summary, author and tags all count. With no search text this falls back to the most recently updated." },
  { value: "updated_desc", label: "Recently updated",
    help: "Newest changes first. Where a work has no recorded update date — common in the bulk imports — its published date is used instead, so it is not pushed to the bottom." },
  { value: "published_desc", label: "Newest",
    help: "Most recently posted first. Reliable for AO3; a large share of FanFiction.net rows arrived without dates and will sort last." },
  // Placed above the raw engagement sorts because it is the one that answers
  // the question people are actually asking when they reach for them.
  { value: "popularity_desc", label: "Most popular",
    help: "The only ranking that compares archives fairly. AO3 kudos, FanFiction.net favourites and FictionAlley hits are different actions counted by different communities on wildly different scales — average kudos is 190 on AO3 against 1,676 on FF.net — so ranking on any single raw figure mostly sorts by which site a work came from. This instead scores each work against its OWN archive, then weighs bookmarks and follows highest (keeping a work costs more than liking it), kudos next, comments and hits lowest, and tempers the result for works with only one figure recorded or with a long time to accumulate it. Works with no recorded engagement at all — still most of the index — sort after those that have some, rather than being ranked as unpopular." },
  { value: "kudos_desc", label: "Most kudos",
    help: "Most-liked first, on the raw AO3-style count. Worth knowing: kudos were not included in the bulk imports, so this ranks the small minority whose counts have since been filled in — and because FanFiction.net favourites are counted on a much larger scale, a mid-tier FF.net work can outrank a far better-loved AO3 one. 'Most popular' is the version of this question that accounts for both." },
  { value: "hits_desc", label: "Most hits",
    help: "Most-read first. Same caveat as kudos — hit counts come from re-reading a work's page, so most of the index has none and sorts below those that do." },
  { value: "bookmarks_desc", label: "Most bookmarks",
    help: "Most-saved first. Bookmarks are a stronger signal of quality than hits, because someone chose to keep the work — but only a fraction of the index has the figure." },
  { value: "comments_desc", label: "Most comments",
    help: "Most-discussed first. Tends to favour long multi-chapter works, where readers comment per chapter." },
  { value: "word_count_desc", label: "Longest",
    help: "Longest first. Word counts are present for nearly every work, so unlike the engagement sorts this one covers the whole index." },
  { value: "word_count_asc", label: "Shortest",
    help: "Shortest first — useful for finding one-shots and drabbles. Pair it with a minimum word count to skip the very shortest." },
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
export interface FieldCoverage { ships: number; characters: number }

export interface IndexTotals {
  stories: number
  hosted: number
  total_words: number
  dlp?: number
  hpffa?: number
  indexed_last_hour?: number
  indexed_last_day?: number
  /** Live per-site share of works listing a ship / a character. */
  coverage?: Record<string, FieldCoverage>
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


export interface TopHub { slug: string; name: string; work_count: number }

// The landing page's way in for someone who does not yet have a search in mind.
//
// Same shape of cache as the totals above and for the same reason: the landing
// page and any other caller want the identical list, and this is a route the
// edge caches (see the Cache Rule in deploy/README.md), so asking twice is
// wasted work at both ends. Hubs change when the hub table is rebuilt, which is
// far slower than a browsing session — an hour is comfortably fresh.
const HUBS_TTL_MS = 60 * 60 * 1000

interface HubsCache { value: TopHub[] | null; at: number; inflight: Promise<TopHub[]> | null }

function hubsCache(): HubsCache {
  const w = globalThis as any
  if (!w.__ficatlasHubs) w.__ficatlasHubs = { value: null, at: 0, inflight: null }
  return w.__ficatlasHubs as HubsCache
}

export function getTopHubs(limit = 12): Promise<TopHub[]> {
  const c = hubsCache()
  if (c.value && Date.now() - c.at < HUBS_TTL_MS) return Promise.resolve(c.value)
  if (c.inflight) return c.inflight

  c.inflight = fetch(`/api/hubs?limit=${limit}`)
    .then(r => (r.ok ? r.json() : []))
    .then((d: TopHub[]) => {
      // An empty list is a real answer (a fresh install has no hubs yet) but it
      // is not worth caching over a good one.
      if (Array.isArray(d) && d.length) { c.value = d; c.at = Date.now() }
      return c.value ?? []
    })
    .catch(() => c.value ?? [])
    .finally(() => { c.inflight = null })

  return c.inflight
}


// FictionAlley's five sections, with what each one actually meant to readers.
//
// The archive was not one library but five, and "a Schnoogle fic" carried real
// information — novel-length, plot-driven — in a way "a FictionAlley fic" did
// not. The help text differs per section for the same reason the sort help
// does: a single paragraph covering all five could only say something generic,
// and the useful part is what distinguishes them.
// Which sites actually record which field, as a fraction of that site's rows.
//
// Measured 2026-08-05 against the live index; see the query in the commit that
// added this. These are structural rather than incidental, so they do not drift:
// AO3 has archive warnings and categories as first-class fields with a fixed
// vocabulary, while the FF.net bulk dump has eight columns and neither is among
// them — its 1.7% comes from the minority of rows enriched from other sources.
//
// Used to warn, NOT to hide or disable. 114,155 FF.net works DO carry warnings,
// and a filter that finds a hundred thousand stories is not broken. Baymard's
// finding on filters is that removing an option makes users conclude the site
// cannot do it at all; the failure being avoided here is subtler — a filter that
// looks like it works, returns almost nothing, and gives no clue why.
export const FIELD_COVERAGE: Record<string, Record<string, number>> = {
  warnings:      { ao3: 0.69, ffnet: 0.017, fictionalley: 0.024 },
  categories:    { ao3: 0.65, ffnet: 0.016, fictionalley: 0.021 },
  relationships: { ao3: 0.59, ffnet: 0.013, fictionalley: 0.184 },
  characters:    { ao3: 0.65, ffnet: 0.017, fictionalley: 0.815 },
  // Measured 2026-08-10. Completion is a special case — see statusNote below.
  status:        { ao3: 0.994, ffnet: 0.197, fictionalley: 0.716 },
}

// Counted 2026-08-10. Used only to weight the coverage warnings, so being a few
// thousand out changes nothing — but AO3 had drifted by ~150k, which is enough
// to shift a borderline percentage.
const SITE_SIZE: Record<string, number> = {
  ao3: 13281389, ffnet: 6571972, fictionalley: 29949,
}

/** The completion filter, which does not behave like the other facets.
 *
 *  Coverage alone understates the problem, because the gap is per-VALUE rather
 *  than per-field. Measured across the whole index:
 *
 *      site          complete   in_progress   unknown
 *      ao3          7,568,883     5,638,120    74,386
 *      ffnet        1,293,899             0 5,278,073
 *      fictionalley    21,453             0     8,496
 *
 *  So "Complete" genuinely works across all three archives, while "In Progress"
 *  exists on AO3 and nowhere else — not because the other archives have no
 *  unfinished works (FanFiction.net is full of them) but because the bulk dump
 *  it was imported from has no completion column at all, so those rows are
 *  honestly recorded as unknown rather than guessed at.
 *
 *  A reader filtering for WIPs would otherwise get a silently AO3-only result
 *  set and no way to tell why. That is the same failure the field coverage
 *  warnings exist to prevent, so it is warned about the same way — pointedly,
 *  because a percentage would not convey "this value is absent entirely".
 */
export function statusNote(selected: string[], sites: string[]): string | null {
  const others = sites.filter(s => s !== "ao3")
  if (selected.includes("in_progress") && others.length) {
    const named = others.map(s => SITE_LABEL[s] ?? s).join(" and ")
    return `Only AO3 records whether a work is still in progress, so this returns `
      + `AO3 results only — ${named} came from bulk data with no completion `
      + `field, and those works are recorded as "not stated" rather than guessed `
      + `at. "Complete" does work across all archives.`
  }
  return coverageWarning("status", sites)
}

/** A warning for a filter that is mostly inert given the sites now selected,
 *  or null when it will work well enough to leave unremarked.
 *
 *  Weighted by how big each selected site is, because the question a reader
 *  actually has is "of what I am searching, how much can this even match" —
 *  selecting AO3 and FF.net together is dominated by AO3, and the filter is
 *  fine. Selecting FF.net alone is a different situation entirely. */
export function coverageWarning(field: string, sites: string[]): string | null {
  const cov = FIELD_COVERAGE[field]
  if (!cov || sites.length === 0) return null
  let rows = 0, have = 0
  for (const s of sites) {
    const n = SITE_SIZE[s]
    if (!n) return null                        // unknown site — say nothing
    rows += n
    have += n * (cov[s] ?? 0)
  }
  if (rows === 0) return null
  const pct = have / rows
  if (pct >= 0.15) return null
  const named = sites.length === 1
    ? SITE_LABEL[sites[0]] ?? sites[0]
    : "the sites you have selected"
  return `Only about ${pct < 0.01 ? "1%" : Math.round(pct * 100) + "%"} of ${named} `
    + `works record this, so filtering on it will hide most results. `
    + `It is an AO3 field; other archives rarely publish it.`
}

const SITE_LABEL: Record<string, string> = {
  ao3: "AO3", ffnet: "FanFiction.net", fictionalley: "FictionAlley",
}

/** Share of each site's works carrying a NON-ZERO value for a sortable field.
 *
 *  Measured 2026-08-13. Zero is the important part: these columns are NOT NULL
 *  everywhere, so a coverage check written against NULL reports 100% for all
 *  three sites and sees nothing wrong. The bulk imports simply wrote 0, and a
 *  work with 0 hits is indistinguishable from one nobody has counted.
 *
 *  The engagement figures are the ones worth staring at. Sorting 19.9M works by
 *  kudos orders the 2.6% that have a number and leaves everything else tied at
 *  zero, so the ranking a reader sees is decided entirely by that fraction —
 *  and since FanFiction.net has essentially none, "most hits" across all sites
 *  is in practice "AO3 and FictionAlley first, everything else in arbitrary
 *  order". That is the behaviour; this is what makes it say so.
 */
export const SORT_COVERAGE: Record<string, Record<string, number>> = {
  hits_desc:       { ao3: 0.027,  ffnet: 0.0000, fictionalley: 0.997 },
  kudos_desc:      { ao3: 0.026,  ffnet: 0.0002, fictionalley: 0.001 },
  comments_desc:   { ao3: 0.021,  ffnet: 0.0003, fictionalley: 0.000 },
  bookmarks_desc:  { ao3: 0.020,  ffnet: 0.0002, fictionalley: 0.001 },
  // Present nearly everywhere; listed so the check can say "this one is fine"
  // rather than staying silent for two different reasons.
  word_count_desc: { ao3: 0.692,  ffnet: 0.9999, fictionalley: 0.9999 },
}

/** A plain warning for a sort whose data most of the selection does not have.
 *
 *  Deliberately names the worst site rather than an average. An average across
 *  AO3 + FF.net hides the fact that one of them contributes nothing at all, and
 *  "3% of your selection" reads as merely thin where "FanFiction.net records
 *  this for almost none of its works" explains the result on screen.
 */
export function sortCoverageNote(sort: string, sites: string[]): string | null {
  const cov = SORT_COVERAGE[sort]
  if (!cov || sites.length === 0) return null

  let rows = 0, have = 0
  for (const s of sites) {
    const n = SITE_SIZE[s]
    if (!n) return null
    rows += n
    have += n * (cov[s] ?? 0)
  }
  if (rows === 0) return null
  const pct = have / rows
  if (pct >= 0.5) return null                 // word counts and the like

  // The sites contributing essentially nothing, largest first — those are the
  // ones whose absence from the top of the results needs explaining.
  const empty = sites.filter(s => (cov[s] ?? 0) < 0.01)
                     .sort((a, b) => (SITE_SIZE[b] ?? 0) - (SITE_SIZE[a] ?? 0))
  const shown = pct < 0.01 ? "under 1%" : `about ${Math.round(pct * 100)}%`

  if (empty.length) {
    const names = empty.map(s => SITE_LABEL[s] ?? s).join(" and ")
    return `Only ${shown} of the works you have selected record this, and `
      + `${names} almost never ${empty.length > 1 ? "do" : "does"} — so those `
      + `works sort below every work that has a figure, whatever they are `
      + `actually worth. This ranks the minority that has been counted.`
  }
  return `Only ${shown} of the works you have selected record this, so this `
    + `ranks that minority; everything else is tied and sorts below it.`
}

// `short` is the one-line version for the help bubble's list; `help` is the
// full sentence, used as the pill's own tooltip.
export const FICALLEY_SECTIONS = [
  { value: "Schnoogle", label: "Schnoogle", short: "novel-length work",
    help: "Novel-length work. FictionAlley's flagship section, for long multi-chapter stories rather than one-shots — the closest thing the archive had to a bookshelf of novels." },
  { value: "The Dark Arts", label: "The Dark Arts", short: "horror and darkfic; the largest section",
    help: "Horror, darkfic and the grimmer end of the archive. The largest section by some way, at nearly 12,000 works." },
  { value: "Astronomy Tower", label: "Astronomy Tower", short: "romance and adult work",
    help: "Romance and adult work. Named for where students were traditionally caught doing exactly that, and second only to The Dark Arts in size." },
  { value: "Riddikulus", label: "Riddikulus", short: "humour and parody",
    help: "Humour and parody — named for the spell that turns a boggart into something laughable." },
  { value: "Essays & Meta", label: "Essays & Meta", short: "essays and analysis, not fiction",
    help: "Analysis and discussion rather than fiction: essays on the books, character studies, arguments about canon. The smallest section, at 66 pieces." },
]

/** A title the AO3 metadata dump cut off mid-phrase, shown as cut off.
 *
 *  About 306,000 works arrived with the title truncated at a word boundary:
 *
 *      One Foot in Front of the
 *      Ill Be Your Light In The
 *      BRING ME THE HEAD OF THE
 *
 *  Rendered plainly these do not read as damaged data, they read as a site full
 *  of badly named stories — which is worse, because the fault looks like the
 *  author's and there is nothing to tell a reader otherwise.
 *
 *  Hiding the works instead was the other option and is the wrong trade: the
 *  summary, tags, length and link are all intact, so hiding costs a reader
 *  306,000 findable stories to tidy up a string. An ellipsis says the same thing
 *  honestly and costs nothing.
 *
 *  The test deliberately matches TRUNCATED_SQL in backend/ao3_title_repair.py.
 *  If the two drift, this marks titles the repair queue will never come for, or
 *  stays silent on ones it is actively fixing. Titles ending in "to", "in" or
 *  "her" are NOT included, for the same reason they are not in the queue: "a
 *  pain that i'm used to" and "that's how the light gets in" are real titles,
 *  and there are far more of those than truncations.
 *
 *  This resolves itself. ao3_title_repair.py refetches these from AO3 and writes
 *  the full title back, so a repaired work simply stops matching.
 */
const TRUNCATED_TAIL = /\s(and|of|the|with)$/i

export function looksTruncated(title: string | null | undefined): boolean {
  return !!title && TRUNCATED_TAIL.test(title.trim())
}

export function displayTitle(title: string | null | undefined): string {
  const t = (title ?? "").trim()
  return looksTruncated(t) ? `${t}…` : t
}
