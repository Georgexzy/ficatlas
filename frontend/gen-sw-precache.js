// gen-sw-precache.js — run AFTER `next build`.
// Reads public/sw.template.js (the source, with a __PRECACHE_MANIFEST__
// placeholder), scans the build output for every static asset, and writes the
// finished service worker to public/sw.js. Using a separate template means this
// is idempotent — it can run on every rebuild without consuming the placeholder.

const fs = require("fs")
const path = require("path")

const STATIC_DIR = path.join(".next", "static")
const TEMPLATE = path.join("public", "sw.template.js")
const OUT = path.join("public", "sw.js")

function walk(dir, baseUrl) {
  let urls = []
  let entries
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true })
  } catch {
    return urls
  }
  for (const e of entries) {
    const full = path.join(dir, e.name)
    const seg = e.name.replace(/\[/g, "%5B").replace(/\]/g, "%5D")
    const url = baseUrl + "/" + seg
    if (e.isDirectory()) {
      urls = urls.concat(walk(full, url))
    } else if (/\.(?:js|css|woff2?|ttf|otf)$/.test(e.name)) {
      urls.push(url)
    }
  }
  return urls
}

const assetUrls = walk(STATIC_DIR, "/_next/static")

// Static page shells.
//
// Measured, because "add the pages a reader might want" is how a precache
// becomes several megabytes of someone's data plan. HTML as served:
//   /            19 KB     /library  20 KB     /settings  19 KB
//   /login       14 KB     /about    30 KB     /permissions  20 KB
//   /account     308 — a redirect, not a page. It was here and cost 12 KB to
//               store a redirect that leads to /library, which is already in
//               this list. Dropped rather than left as dead weight.
//   /fandoms     2.0 MB    /ships    3.0 MB
//
// The two browse indexes are the reason this list is not "everything": they
// render one row per hub, which is 5,025 and 6,165 rows. Precaching them would
// add ~5 MB to every install, on a phone, to make two list pages available with
// no connection. An individual hub is 350-400 KB for the same reason — they are
// pages to be CACHED WHEN VISITED, which the navigation branch does, not pages
// to be shipped to every reader whether they want them or not. A failed
// `cache.add` on any entry here is recorded and ignored unless it is ESSENTIAL,
// so a 3 MB entry that timed out would not have broken install; it would simply
// have quietly not been there.
const pageUrls = ["/", "/library", "/login", "/settings", "/about", "/permissions"]

// The story and reader routes are dynamic — one URL per story — so no specific
// instance can be precached. But every /story/<id> and /story/<id>/chapter/<n>
// renders the SAME client shell, which then loads the story from IndexedDB. So
// we precache one placeholder instance of each and the service worker serves it
// for any story path when offline.
//
// Without this, saving a story for offline reading and then opening it with no
// connection fell through to the generic "you're offline" page: the reader's JS
// chunk was cached, but the HTML shell that loads it never was.
const shellUrls = ["/story/offline-shell", "/story/offline-shell/chapter/1"]

const manifest = [...new Set([...pageUrls, ...shellUrls, ...assetUrls])]

if (!fs.existsSync(TEMPLATE)) {
  console.error(`gen-sw-precache: ${TEMPLATE} not found`); process.exit(1)
}
let sw = fs.readFileSync(TEMPLATE, "utf8")
if (!sw.includes("__PRECACHE_MANIFEST__")) {
  console.error("gen-sw-precache: placeholder __PRECACHE_MANIFEST__ not found in template")
  process.exit(1)
}
sw = sw.split("__PRECACHE_MANIFEST__").join(JSON.stringify(manifest))

// One stamp shared by the service worker cache name and build.json below, so
// the version a client reports always matches the cache it is actually using.
const buildStamp = new Date().toISOString()
if (!sw.includes("__CACHE_VERSION__")) {
  console.error("gen-sw-precache: placeholder __CACHE_VERSION__ not found in template")
  process.exit(1)
}
sw = sw.split("__CACHE_VERSION__").join(
  "ficatlas-shell-" + buildStamp.replace(/[^0-9]/g, "").slice(0, 14))
fs.writeFileSync(OUT, sw)
console.log(`gen-sw-precache: wrote ${OUT} with ${manifest.length} precached URLs`)

// A build stamp the running app can show. Without one there is no way to tell a
// stale cached bundle from a genuine bug — a fix can be live on the server while
// an installed PWA still runs the JS and CSS it cached days ago, and the two are
// indistinguishable from the outside.
fs.writeFileSync(
  path.join("public", "build.json"),
  JSON.stringify({ built: buildStamp }) + "\n",
)
console.log("gen-sw-precache: wrote public/build.json")
