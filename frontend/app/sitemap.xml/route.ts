import { loadHubs, childNames, newest, sitemapindex, xml, SITE, split, chunk, CORE_HUBS }
  from "@/lib/sitemapData"

// A sitemap INDEX, not a flat list. This replaced a single file of 11,196 URLs,
// and the reason is the one thing this site can actually control about how its
// tiny crawl budget gets spent.
//
// The flat file changed every day. `content_at` is the <lastmod> and it moved
// whenever a hub's work_count moved, so with the crawler adding ~15,000 works a
// day it moved for almost every popular hub: measured 2026-09-08, 3,185 of
// 6,165 ship hubs stamped that day. hub_build.py now only stamps a real change,
// which fixes the per-URL lie — but it does not fix the file-level one. Even
// with honest timestamps, ONE hub changing means the only file this site
// publishes has changed, and a crawler learning that has to re-read all 11,196
// URLs to find out which.
//
// An index makes the question cheap. Each child carries its own <lastmod>, so a
// crawler re-fetches the children that moved and skips the rest, and the churn
// is contained in whichever 2,000-URL file it landed in instead of touching
// everything. Search Console reports coverage per child too, which is the only
// way to see whether the hubs that matter are getting in — against one file of
// 11,196 the answer was a single number that said nothing.
//
// Worth being clear about what this does NOT do: it does not make Google crawl
// more. Googlebot took 2 URLs from this origin in the 9.7h window measured on
// 2026-09-08 and it is not being blocked — the one request it made was a
// verified 66.249.74.39 getting a 304 on robots.txt. That is crawl DEMAND on a
// young domain, which no file on this server can set. What the index does is
// make sure the budget that does arrive is not spent re-reading 11,196
// unchanged URLs to find the handful that moved.
//
// Story pages are still deliberately absent. See robots.txt: 20.5M works would
// be ~400 files at the protocol limit and an invitation to crawl the whole
// index off a home connection.
export const dynamic = "force-dynamic"

export async function GET() {
  const { fandoms, ships } = await loadHubs()
  const s = split(ships), f = split(fandoms)

  // Each child's lastmod is the newest content_at it actually contains, so the
  // index tells the truth at file granularity the same way the entries do at
  // URL granularity. `core` also covers the static pages, which have no
  // content_at — it takes the hub answer, since that is the part that moves.
  const stamp = (name: string): string | undefined => {
    if (name === "core") return newest([...s.core, ...f.core])
    const m = /^(ships|fandoms)-(\d+)$/.exec(name)
    if (!m) return undefined
    return newest(chunk(m[1] === "ships" ? s.bulk : f.bulk, Number(m[2])))
  }

  return xml(sitemapindex(
    childNames({ ships: s.bulk.length, fandoms: f.bulk.length }).map(name => ({
      loc: `${SITE}/sitemaps/${name}.xml`,
      lastmod: stamp(name),
    })),
  ))
}
