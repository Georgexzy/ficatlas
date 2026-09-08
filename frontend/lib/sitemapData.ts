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
    const r = await fetch(`${INTERNAL_API}${path}?limit=10000`, {
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

export const loadHubs = () =>
  Promise.all([listing("/api/hubs"), listing("/api/ships")])
    .then(([fandoms, ships]) => ({ fandoms, ships }))

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
export function childNames(n: { fandoms: number; ships: number }): string[] {
  const names = ["core"]
  for (let i = 0; i * CHUNK < n.ships; i++)   names.push(`ships-${i + 1}`)
  for (let i = 0; i * CHUNK < n.fandoms; i++) names.push(`fandoms-${i + 1}`)
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
