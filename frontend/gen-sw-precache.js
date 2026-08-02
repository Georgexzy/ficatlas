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
const pageUrls = ["/", "/library", "/login", "/settings", "/account"]

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
fs.writeFileSync(OUT, sw)
console.log(`gen-sw-precache: wrote ${OUT} with ${manifest.length} precached URLs`)
