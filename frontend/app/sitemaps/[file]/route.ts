import { loadHubs, newest, urlset, xml, SITE, split, chunk, type Hub }
  from "@/lib/sitemapData"

// The children of /sitemap.xml. See that file for why this is segmented at all.
//
// One route rather than a file per segment because the segments are decided by
// how many hubs exist, which is data and not something to encode as routes.
export const dynamic = "force-dynamic"

const STATIC = ["/", "/fandoms", "/ships", "/about", "/permissions", "/takedown"]

// lastmod is the hub's content_at — when its contents actually changed, not
// when it was last rebuilt. Google states plainly that it uses lastmod only when
// it is consistently accurate, so stamping every page with the nightly rebuild
// time would get the whole field discarded. Omitted rather than faked when there
// is none: no lastmod is a normal sitemap, a wrong one is a reason to stop
// trusting the file. See the column note in backend/init_db.py.
const entry = (path: string) => (h: Hub) => ({
  loc: `${SITE}${path}/${h.slug}`,
  ...(h.content_at ? { lastmod: new Date(h.content_at).toISOString() } : {}),
})

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ file: string }> },
) {
  const { file } = await params
  const name = file.replace(/\.xml$/, "")
  const { fandoms, ships } = await loadHubs()
  const s = split(ships), f = split(fandoms)

  if (name === "core") {
    // The static pages carry no lastmod of their own, so they borrow the newest
    // hub stamp in this file rather than claiming a date nothing can support.
    // The alternative — now() — is the exact lie the rest of this avoids.
    const lastmod = newest([...s.core, ...f.core])
    return xml(urlset([
      ...STATIC.map(p => ({ loc: `${SITE}${p}`, ...(lastmod ? { lastmod } : {}) })),
      ...s.core.map(entry("/ship")),
      ...f.core.map(entry("/fandom")),
    ]))
  }

  const m = /^(ships|fandoms)-(\d+)$/.exec(name)
  if (m) {
    const isShips = m[1] === "ships"
    const slice = chunk(isShips ? s.bulk : f.bulk, Number(m[2]))
    // A number past the end is a 404 rather than an empty urlset: an empty file
    // in a crawler's hands is a page that exists and holds nothing, which is a
    // worse answer than "this is not one of my sitemaps".
    if (slice.length === 0) return new Response("Not found", { status: 404 })
    return xml(urlset(slice.map(entry(isShips ? "/ship" : "/fandom"))))
  }

  return new Response("Not found", { status: 404 })
}
