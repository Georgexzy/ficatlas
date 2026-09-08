import { describe, it, expect } from "vitest"
import { split, chunk, childNames, newest, urlset, sitemapindex,
         CHUNK, CORE_HUBS, type Hub } from "./sitemapData"

// The sitemap is read by crawlers and by nobody else, so nothing about it fails
// visibly. The whole file is only ever wrong in Search Console, six weeks later.

const hubs = (n: number, from = 0): Hub[] =>
  Array.from({ length: n }, (_, i) => ({
    slug: `hub-${String(n - i + from).padStart(6, "0")}`,   // work_count order
    content_at: `2026-09-0${(i % 8) + 1}T00:00:00.000Z`,
  }))

describe("split", () => {
  it("puts the top CORE_HUBS in core and everything else in bulk, once", () => {
    const all = hubs(2500)
    const { core, bulk } = split(all)
    expect(core).toHaveLength(CORE_HUBS)
    expect(bulk).toHaveLength(2500 - CORE_HUBS)
    // No URL in two files: this is what makes Search Console's per-file
    // coverage numbers mean something.
    const seen = new Set([...core, ...bulk].map(h => h.slug))
    expect(seen.size).toBe(2500)
  })

  it("keeps core in the API's work_count order", () => {
    const all = hubs(600)
    expect(split(all).core.map(h => h.slug)).toEqual(all.slice(0, CORE_HUBS).map(h => h.slug))
  })

  it("sorts bulk by slug, not by work_count", () => {
    const { bulk } = split(hubs(1200))
    const sorted = [...bulk].sort((a, b) => a.slug.localeCompare(b.slug))
    expect(bulk.map(h => h.slug)).toEqual(sorted.map(h => h.slug))
  })

  // The regression the split exists to prevent. work_count moves for most hubs
  // daily; if the bulk were ordered by it, a hub near a chunk boundary would
  // cross it and change the contents of two files on a day when no page
  // changed — which is the daily-rewrite problem all over again, one level up.
  it("does not move a hub between files when only work_count order changes", () => {
    const all = hubs(3000)
    const shuffled = [...all.slice(0, CORE_HUBS), ...[...all.slice(CORE_HUBS)].reverse()]
    const a = split(all), b = split(shuffled)
    for (const n of [1, 2]) {
      expect(chunk(b.bulk, n).map(h => h.slug)).toEqual(chunk(a.bulk, n).map(h => h.slug))
    }
  })

  it("handles fewer hubs than one core file", () => {
    const { core, bulk } = split(hubs(10))
    expect(core).toHaveLength(10)
    expect(bulk).toHaveLength(0)
    expect(childNames({ ships: 0, fandoms: 0 })).toEqual(["core"])
  })
})

describe("chunk / childNames", () => {
  it("names exactly the chunks that have contents", () => {
    const names = childNames({ ships: CHUNK + 1, fandoms: 0 })
    expect(names).toEqual(["core", "ships-1", "ships-2"])
  })

  it("returns empty past the end, so the route can 404 rather than serve nothing", () => {
    expect(chunk(hubs(10), 2)).toEqual([])
  })

  it("covers every bulk hub across its chunks with no overlap", () => {
    const { bulk } = split(hubs(7000))
    const names = childNames({ ships: bulk.length, fandoms: 0 })
      .filter(n => n.startsWith("ships-"))
    const got = names.flatMap(n => chunk(bulk, Number(n.split("-")[1])))
    expect(got).toHaveLength(bulk.length)
    expect(new Set(got.map(h => h.slug)).size).toBe(bulk.length)
  })
})

describe("newest", () => {
  it("is the greatest content_at, so a file's lastmod is true of its contents", () => {
    expect(newest([
      { slug: "a", content_at: "2026-09-01T00:00:00.000Z" },
      { slug: "b", content_at: "2026-09-07T12:00:00.000Z" },
      { slug: "c", content_at: "2026-09-03T00:00:00.000Z" },
    ])).toBe("2026-09-07T12:00:00.000Z")
  })

  // Undefined rather than now(). A wrong lastmod is a reason for a crawler to
  // stop trusting the whole file; an absent one is an ordinary sitemap.
  it("is undefined when nothing has a stamp", () => {
    expect(newest([{ slug: "a" }])).toBeUndefined()
    expect(newest([])).toBeUndefined()
  })
})

describe("serialisation", () => {
  it("emits a urlset and omits lastmod when absent", () => {
    const x = urlset([{ loc: "https://e/x", lastmod: "2026-09-01T00:00:00.000Z" },
                      { loc: "https://e/y" }])
    expect(x).toContain("<url><loc>https://e/x</loc><lastmod>2026-09-01T00:00:00.000Z</lastmod></url>")
    expect(x).toContain("<url><loc>https://e/y</loc></url>")
    expect(x).toContain('xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"')
  })

  it("emits a sitemapindex, not a urlset", () => {
    const x = sitemapindex([{ loc: "https://e/sitemaps/core.xml" }])
    expect(x).toContain("<sitemapindex")
    expect(x).not.toContain("<urlset")
  })

  // Slugs come from tag names and reach these files unescaped otherwise. One
  // ampersand makes the document malformed and a crawler drops the whole file,
  // not the one entry.
  it("escapes XML metacharacters in a loc", () => {
    const x = urlset([{ loc: "https://e/ship/rock-&-roll<>" }])
    expect(x).toContain("rock-&amp;-roll&lt;&gt;")
    expect(x).not.toMatch(/roll</)
  })
})
