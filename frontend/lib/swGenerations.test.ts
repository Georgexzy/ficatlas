/** The offline cache must survive a deploy, and must not grow without bound.
 *
 *  `activate` used to delete every cache whose name was not the current one. That
 *  is correct for hashed build assets — superseded chunks are dead weight and
 *  must go — and wrong for everything else, because a reader's offline world is
 *  made of PAGES THEY OPENED, and every deploy threw all of them away. Several
 *  times a day, so in practice "the pages I have open" meant "the pages I have
 *  opened since lunchtime", and the fallback page's promise ("open it once while
 *  online") was true only until the next build.
 *
 *  Nothing about that is a cache-size problem. The pages are already on the
 *  device and already paid for. So `KEEP_GENERATIONS` is 2 rather than 1.
 *
 *  Two rather than an unbounded list, and the reason is not tidiness:
 *  `activate` deletes everything older than the retained window, so an unbounded
 *  list would mean an unbounded amount of superseded chunks on the device. The
 *  browser evicts a cache long before a reader gets three builds deep offline.
 *
 *  A retained generation is safe precisely because it is retained WHOLE. Cached
 *  HTML references the `/_next/static/…` chunk filenames of the build that
 *  produced it, so an old page served without its old chunks is a shell whose
 *  JavaScript 404s — it renders as markup and never boots. That is why reads
 *  span generations (`matchAny`, `cachedStoryShell`) while every WRITE still goes
 *  to the current one, so a stale generation can never collect new entries.
 *
 *  A source assertion rather than a render test, per vitest.config.ts. The
 *  failure mode is also invisible to a single-page test: it needs a build to land
 *  between two visits, which is why it survived as long as it did.
 */
import { describe, expect, it } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

const SW = readFileSync(
  path.resolve(__dirname, "../public/sw.template.js"), "utf8")
const GEN = readFileSync(
  path.resolve(__dirname, "../gen-sw-precache.js"), "utf8")

const activate = SW.slice(
  SW.indexOf('self.addEventListener("activate"'),
  SW.indexOf('self.addEventListener("fetch"'))
const nav = SW.slice(SW.indexOf('if (request.mode === "navigate")'))
const precachePages = GEN.slice(GEN.indexOf("const pageUrls"),
                                GEN.indexOf("const shellUrls"))
const shells = GEN.slice(GEN.indexOf("const shellUrls"),
                         GEN.indexOf("const manifest"))

describe("cache generations", () => {
  it("keeps more than one", () => {
    const n = Number(SW.match(/const KEEP_GENERATIONS = (\d+)/)?.[1])
    expect(n).toBeGreaterThan(1)
  })

  it("deletes only its own caches, and only beyond the window", () => {
    // The old line was `keys.filter((k) => k !== CACHE)` — every cache on the
    // origin, including anything this worker never wrote.
    expect(activate).toContain("CACHE_PREFIX")
    expect(activate).toContain("ours.slice(KEEP_GENERATIONS)")
    expect(activate).not.toMatch(/keys\.filter\(\(k\) => k !== CACHE\)/)
  })

  it("verifies the CURRENT cache before deleting anything", () => {
    // Checking across generations here would let a half-built cache pass by
    // finding the previous build's chunks, and then delete them. This is the
    // only `cache.match(` left in the file, and it is deliberate.
    expect(SW.match(/cache\.match\(/g) || []).toHaveLength(1)
    expect(activate).toContain("cache.match(p)")
  })

  it("reads across generations everywhere a reader can reach", () => {
    // The navigation branch and the last-resort shells are what is reachable
    // offline; both must span. The hashed-asset branch must too, because that is
    // what makes a retained generation usable at all.
    expect(nav).toContain("await caches.open(CACHE)")
    expect(nav).toContain("matchAny(request)")
    expect(nav).toContain('matchAny("/library")')
    expect(nav).toContain('matchAny("/")')
    expect(nav).toContain("c.put(request, copy.clone())")  // writes: current only

    const assets = SW.slice(SW.indexOf("if (isHashedAsset)"),
                            SW.indexOf('if (url.pathname.startsWith("/_next/"))'))
    expect(assets).toContain("matchAny(request)")

    // The story shells' fallback is "any real visited story", and one visited
    // before the last deploy lives in the previous generation.
    expect(SW).toContain("async function cachedStoryShell(caches_, wantChapter)")
    expect(nav).toContain("cachedStoryShell(await readableCaches()")
  })

  it("does not try to precache the pages that are too big to ship", () => {
    // Measured: /fandoms is 2.0 MB and /ships 3.0 MB of HTML, because they
    // render one row per hub. Precaching them would add ~5 MB to every install.
    expect(precachePages).not.toContain("/fandoms")
    expect(precachePages).not.toContain("/ships")
    // And /account is a 308, not a page — 12 KB to store a redirect to /library.
    expect(precachePages).not.toContain('"/account"')
    // The small static pages that were missing, and are cheap.
    expect(precachePages).toContain('"/about"')
    expect(precachePages).toContain('"/permissions"')
  })

  it("still precaches the two story shells", () => {
    // The comment above them records what breaks without it: saving a story and
    // opening it with no connection fell through to the "you're offline" page,
    // because the reader's JS chunk was cached and the HTML shell that loads it
    // never was.
    expect(shells).toContain("/story/offline-shell")
    expect(shells).toContain("/story/offline-shell/chapter/1")
  })
})