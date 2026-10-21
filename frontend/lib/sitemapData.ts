// Shared loader for the sitemap index and its children.
//
// Both routes need the same two hub listings, and Next's fetch cache is what
// keeps that from being two queries per crawler request: the revalidate window
// is shared across routes because it is keyed on the URL, so the index and all
// of its children served in one crawl pass hit the cache after the first.
const INTERNAL_API = process.env.INTERNAL_API_URL || "http://backend:8000"
export const SITE = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000"

export const CACHE_S = 86400

export interface Hub { slug: string; content_at?: string }

async function listing(path: string): Promise<Hub[]> {
  try {
    const r = await fetch(`${INTERNAL_API}${path}?limit=50000`, {
      next: { revalidate: CACHE_S },
      headers: { "x-internal-render": process.env.INTERNAL_RENDER_TOKEN || "" },
      signal: AbortSignal.timeout(15000),
    })
    return r.ok ? await r.json() : []
  } catch {
    // A sitemap that 500s is worse than one that lists fewer pages.
    return []
  }
}

// How many series pages the sitemap lists, out of the 355,139 that exist.
//
// Series URLs were 93.6% of everything this site declared: 178 of 192 files,
// each a UUID path to a page of two or three works, while the 24,264 hub pages
// that can actually rank got 13 files. Measured 2026-10-21 — over 68 days of
// landing data Google's 177 visitors arrived through 159 hub pages and 12
// through home, not one through a series page; and in the 24h origin log
// bingbot took 230 series URLs against 84 hub URLs. The declared surface and
// the useful surface were pointing in opposite directions.
//
// 10,000 is the same number author hubs use (AUTHOR_HUB_LIMIT): enough that a
// crawler meets recent series through the sitemap instead of hunting for them
// through 178 files, small enough that the hubs are not a rounding error next
// to it. Newest-first by content_at is the selection, because a series created
// last week is the one still being linked and shared; older ones stay reachable
// through the story pages that carry them, the /series browse and IndexNow.
export const SERIES_SITEMAP_LIMIT = 10000

/** The newest `n` entries, returned in slug order like every other bulk list.
 *
 * The slug sort is not tidiness — see `split` for why bulk order has to be
 * something that does not move. Selection by content_at is the only part that
 * shifts as the set grows, and a series arriving is a real change, the same as
 * a hub crossing into or out of core.
 *
 * A missing stamp counts as newest rather than oldest. `created_at` is NOT NULL
 * so this is belt and braces, but the direction matters: the cost of listing
 * one extra URL is nothing, and the cost of silently excluding a real series
 * because a lookup came back empty is a coverage claim the sitemap cannot keep. */
export function capNewest(hubs: Hub[], n: number = SERIES_SITEMAP_LIMIT): Hub[] {
  if (hubs.length <= n) return [...hubs].sort((a, b) => a.slug.localeCompare(b.slug))
  const kept = [...hubs]
    .sort((a, b) => {
      if (!a.content_at) return b.content_at ? -1 : a.slug.localeCompare(b.slug)
      if (!b.content_at) return 1
      return b.content_at.localeCompare(a.content_at)
    })
    .slice(0, n)
  return kept.sort((a, b) => a.slug.localeCompare(b.slug))
}

export const loadHubs = () =>
  Promise.all([listing("/api/hubs"), listing("/api/ships"), listing("/api/hubs/topics/all"),
               listing("/api/authors"), listing("/api/stories/series")])
    .then(([fandoms, ships, topics, authors, series]) => ({
      fandoms, ships,
      // Trope pages (backend/topic_hubs.py), already in slug order. Their own
      // files, so their churn never rewrites the fandom or pairing files.
      topics: [...topics].sort((a, b) => a.slug.localeCompare(b.slug)),
      // Author pages (backend/author_hubs.py), in slug order for the same reason.
      authors: [...authors].sort((a, b) => a.slug.localeCompare(b.slug)),
      // Series pages (backend/stories.py): the newest SERIES_SITEMAP_LIMIT only.
      series: capNewest(series),
    }))

// How many URLs go in one child file.
//
// The protocol's limit is 50,000 and every file here is far under it, so this
// number is not about the limit. It is about how much a crawler has to re-fetch
// to learn that ONE hub changed: with a single flat file that answer was 11,196
// URLs, and with 2,000 it is 2,000. Small enough to isolate churn, large enough
// that the index does not become a directory of near-empty files.
export const CHUNK = 2000

// The lead file: everything static, plus the hubs most worth having in an index
// at all. Kept small and kept SEPARATE from the bulk, because these are the
// pages whose indexation actually matters and a sitemap index is the only place
// this site can say so in a way Search Console will report back per file.
export const CORE_HUBS = 500

/** How the hubs of one kind are split across files.
 *
 * `core` is the top CORE_HUBS by work_count, which is the order the API already
 * returns. The bulk is EVERYTHING ELSE SORTED BY SLUG, and the sort is the
 * whole point rather than tidiness.
 *
 * Ordering the bulk by work_count the way the API does would undo the reason
 * these files were split up. work_count moves for most hubs every day, so a hub
 * sitting near a 2,000-URL boundary changes rank, crosses it, and lands in a
 * different file — which makes BOTH files' contents change on a day when no
 * page changed at all. Segmenting to isolate churn and then ordering the
 * segments by the churning value gets back exactly the daily-rewrite problem
 * the split exists to remove. Slug order does not move.
 *
 * Membership still shifts when a hub crosses into or out of the top 500, which
 * is a slow set, and when hubs are added or removed. Both are real changes.
 */
export function split(hubs: Hub[]): { core: Hub[]; bulk: Hub[] } {
  const core = hubs.slice(0, CORE_HUBS)
  const bulk = hubs.slice(CORE_HUBS).sort((a, b) => a.slug.localeCompare(b.slug))
  return { core, bulk }
}

/** The nth chunk of a bulk list, 1-indexed. Empty past the end. */
export const chunk = (bulk: Hub[], n: number) =>
  bulk.slice((n - 1) * CHUNK, n * CHUNK)

/** The child files this site publishes, in the order the index lists them.
 *
 * Counts are of the BULK, not the whole set — core is carved out first and no
 * URL appears in two files. Overlap would be legal (crawlers dedupe) but it
 * would make Search Console's per-file coverage double-count the top 500, and
 * being able to read those numbers is most of why the index exists. */
export function childNames(n: { fandoms: number; ships: number; topics?: number; authors?: number; series?: number }): string[] {
  const names = ["core"]
  for (let i = 0; i * CHUNK < n.ships; i++)   names.push(`ships-${i + 1}`)
  for (let i = 0; i * CHUNK < n.fandoms; i++) names.push(`fandoms-${i + 1}`)
  for (let i = 0; i * CHUNK < (n.topics ?? 0); i++) names.push(`topics-${i + 1}`)
  for (let i = 0; i * CHUNK < (n.authors ?? 0); i++) names.push(`authors-${i + 1}`)
  for (let i = 0; i * CHUNK < (n.series ?? 0); i++) names.push(`series-${i + 1}`)
  return names
}

const esc = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")

export interface Entry { loc: string; lastmod?: string }

export function urlset(entries: Entry[]): string {
  const body = entries.map(e =>
    `<url><loc>${esc(e.loc)}</loc>`
    + (e.lastmod ? `<lastmod>${e.lastmod}</lastmod>` : "")
    + `</url>`).join("")
  return `<?xml version="1.0" encoding="UTF-8"?>`
    + `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${body}</urlset>`
}

export function sitemapindex(children: Entry[]): string {
  const body = children.map(c =>
    `<sitemap><loc>${esc(c.loc)}</loc>`
    + (c.lastmod ? `<lastmod>${c.lastmod}</lastmod>` : "")
    + `</sitemap>`).join("")
  return `<?xml version="1.0" encoding="UTF-8"?>`
    + `<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${body}</sitemapindex>`
}

/** The newest content_at in a set, as a W3C datetime, or undefined if none. */
export function newest(hubs: Hub[]): string | undefined {
  let best: string | undefined
  for (const h of hubs) {
    if (h.content_at && (best === undefined || h.content_at > best)) best = h.content_at
  }
  return best ? new Date(best).toISOString() : undefined
}

export const xml = (body: string) =>
  new Response(body, {
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      // Long shared cache with a long stale window: these are crawler routes
      // rebuilt offline, and a crawler holding a copy an hour old costs nothing
      // while a crawler waiting on a cold render costs a request it will not
      // spend again today.
      "Cache-Control": "public, max-age=0, must-revalidate, s-maxage=3600, stale-while-revalidate=86400",
    },
  })
