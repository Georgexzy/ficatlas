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
    { url: `${SITE}/ships`, changeFrequency: "weekly", priority: 0.8 },
    { url: `${SITE}/about`, changeFrequency: "monthly", priority: 0.5 },
    { url: `${SITE}/permissions`, changeFrequency: "monthly", priority: 0.5 },
    { url: `${SITE}/takedown`, changeFrequency: "monthly", priority: 0.3 },
  ]

  // Both hub kinds, fetched independently: one of them being unavailable should
  // cost its own entries and not the other's.
  const listing = async (path: string): Promise<{ slug: string; content_at?: string }[]> => {
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

  const [fandoms, ships] = await Promise.all([
    listing("/api/hubs"),
    listing("/api/ships"),
  ])

  // lastModified is the hub's content_at — when its contents actually changed,
  // not when it was last rebuilt. Google states plainly that it uses lastmod
  // only when it is consistently accurate, so stamping every page with the
  // nightly rebuild time would get the whole field discarded and would be a
  // lie about 7,584 pages besides. See the column note in backend/init_db.py.
  //
  // Omitted rather than faked when the API does not supply one: no lastmod is a
  // normal sitemap, a wrong one is a reason to stop trusting the file.
  const entry = (path: string, h: { slug: string; content_at?: string }) => ({
    url: `${SITE}${path}/${h.slug}`,
    ...(h.content_at ? { lastModified: new Date(h.content_at) } : {}),
    changeFrequency: "weekly" as const,
    priority: 0.7,
  })

  return [
    ...staticPages,
    ...fandoms.map(h => entry("/fandom", h)),
    ...ships.map(h => entry("/ship", h)),
  ]
}
