import type { MetadataRoute } from "next"

// Hubs and static pages only — deliberately NOT the 19.9M story pages.
//
// A sitemap of every work would be ~400 files at Google's 50k-URL limit and
// would invite a crawl of 19.9M pages against a home connection: the same trap
// robots.txt blocks `/*?` to avoid, entered through a different door. Listing
// the few thousand hubs instead gives a crawler a real entry point and lets it
// discover story pages by following links at whatever rate it chooses, which is
// the rate the site can actually survive.
//
// Story pages are still indexable — they carry per-work metadata and are linked
// from hubs. Not listing them here is a statement about crawl budget, not about
// whether they should be indexed.
const INTERNAL_API = process.env.INTERNAL_API_URL || "http://backend:8000"
const SITE = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000"

// Rendered per request, not at build time.
//
// Next prerenders routes that declare `revalidate`, and a build runs inside a
// docker build layer where the `backend` host does not exist — the fetch hung
// until the 60s export timeout and failed the image three attempts running.
// Prerendering is also wrong on its own terms here: the data comes from a table
// rebuilt offline, so baking it into the image freezes it at whatever the last
// build saw.
//
// The cost is one query per request, and it is a small indexed read over ~5k
// hub rows rather than anything touching the 19.9M-row stories table. These are
// crawler-frequency routes, not the search path.
export const dynamic = "force-dynamic"

const CACHE_S = 86400

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const staticPages: MetadataRoute.Sitemap = [
    { url: `${SITE}/`, changeFrequency: "daily", priority: 1 },
    { url: `${SITE}/fandoms`, changeFrequency: "weekly", priority: 0.8 },
    { url: `${SITE}/about`, changeFrequency: "monthly", priority: 0.5 },
    { url: `${SITE}/permissions`, changeFrequency: "monthly", priority: 0.5 },
    { url: `${SITE}/takedown`, changeFrequency: "monthly", priority: 0.3 },
  ]

  try {
    const r = await fetch(`${INTERNAL_API}/api/hubs?limit=10000`, {
      next: { revalidate: CACHE_S },
      headers: { "x-internal-render": process.env.INTERNAL_RENDER_TOKEN || "" },
      signal: AbortSignal.timeout(15000),
    })
    if (!r.ok) return staticPages
    const hubs: { slug: string }[] = await r.json()
    return [
      ...staticPages,
      ...hubs.map(h => ({
        url: `${SITE}/fandom/${h.slug}`,
        changeFrequency: "weekly" as const,
        priority: 0.7,
      })),
    ]
  } catch {
    // A sitemap that 500s is worse than one that lists only the static pages.
    return staticPages
  }
}
