// FicAtlas service worker (generated from sw.template.js at build time).
// The precache manifest placeholder below is replaced by gen-sw-precache.js
// with the real list of built asset URLs, so install-time caching covers the
// whole app shell — every JS chunk — and the app can cold-start with no network.
//
// Offline strategy:
//   - Precache all build assets + top-level page shells at install.
//   - Hashed assets (/_next/static/*): cache-first (immutable; a hit is always
//     correct, and serving without a network round-trip is what lets the app
//     boot offline).
//   - The reader route /story/<id>/chapter/<n> is dynamic (one URL per story),
//     so it can't be precached per-instance. Instead, any offline navigation to
//     a /story/.../chapter/... URL is served the cached reader SHELL; the JS
//     then boots and reads that specific chapter from IndexedDB.
//   - /api/*: never cached; offline data comes from IndexedDB in the app.

// Bump on any change to the precache set or fetch strategy: `activate` deletes
// every cache whose name differs, which is what evicts the previous shell.
const CACHE = "ficatlas-shell-v8"
const PRECACHE = __PRECACHE_MANIFEST__

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) =>
      // addAll fails atomically if any URL 404s; add individually so one bad
      // entry can't abort the whole precache.
      Promise.all(PRECACHE.map((u) =>
        cache.add(u).catch(() => {}))),
    ),
  )
  self.skipWaiting()
})

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

// Serve a cached story shell for any /story/... URL when offline.
//
// Both story routes are dynamic (one URL per story), so the exact page is only
// in the cache if it happened to be visited online. The build precaches one
// placeholder instance of each shell (/story/offline-shell[/chapter/1]); every
// story renders the same shell and then loads its content from IndexedDB, so any
// cached instance works for any story.
//
// `wantChapter` picks the reader shell vs the story-detail shell — they are
// different pages and serving the wrong one would render the wrong screen.
async function cachedStoryShell(cache, wantChapter) {
  const keys = await cache.keys()
  let fallback = null
  for (const req of keys) {
    const p = new URL(req.url).pathname
    if (!p.startsWith("/story/")) continue
    const isChapter = p.includes("/chapter/")
    if (isChapter !== wantChapter) continue
    const hit = await cache.match(req)
    if (hit) {
      // Prefer the dedicated placeholder; fall back to any real visited story.
      if (p.startsWith("/story/offline-shell")) return hit
      if (!fallback) fallback = hit
    }
  }
  return fallback
}

self.addEventListener("fetch", (event) => {
  const { request } = event
  const url = new URL(request.url)

  if (url.pathname.startsWith("/api/")) return
  // Never cache the build stamp — it exists precisely to reveal a stale cache.
  if (url.pathname === "/build.json") return
  if (request.method !== "GET" || url.origin !== self.location.origin) return

  // Immutable hashed assets → cache-first.
  const isHashedAsset =
    url.pathname.startsWith("/_next/static/") ||
    url.pathname.match(/\.(?:js|css|woff2?|ttf|otf|png|svg|ico|webp|jpg|jpeg|gif)$/)

  if (isHashedAsset) {
    event.respondWith(
      caches.open(CACHE).then(async (cache) => {
        const hit = await cache.match(request)
        if (hit) return hit
        try {
          const res = await fetch(request)
          if (res && res.status === 200) cache.put(request, res.clone()).catch(() => {})
          return res
        } catch {
          return new Response("", { status: 504, statusText: "offline" })
        }
      }),
    )
    return
  }

  // Other /_next/* (RSC/data) → network, fall back to cache.
  if (url.pathname.startsWith("/_next/")) {
    event.respondWith(fetch(request).catch(() => caches.match(request)))
    return
  }

  // Navigations → network-first; offline, fall back to cache. For the dynamic
  // reader route, serve any cached reader shell so the JS can boot and read the
  // requested chapter from IndexedDB.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((res) => {
          const copy = res.clone()
          caches.open(CACHE).then((c) => {
            c.put(request, copy.clone()).catch(() => {})
            c.put(url.origin + url.pathname, copy).catch(() => {})
          }).catch(() => {})
          return res
        })
        .catch(async () => {
          const cache = await caches.open(CACHE)
          // Exact, then pathname-only.
          let hit = (await cache.match(request)) ||
                    (await cache.match(url.origin + url.pathname))
          if (hit) return hit
          // Dynamic story routes: serve the matching cached shell, which then
          // reads the story from IndexedDB.
          if (url.pathname.startsWith("/story/")) {
            hit = await cachedStoryShell(cache, url.pathname.includes("/chapter/"))
            if (hit) return hit
          }
          // Known shells, then a friendly fallback.
          return (await cache.match("/library")) ||
            (await cache.match("/")) ||
            new Response(
              "<!doctype html><meta charset=utf-8><title>Offline</title>" +
              "<body style='font-family:system-ui;background:#0e0e10;color:#eee;padding:2rem'>" +
              "<h1>You're offline</h1><p>This page wasn't saved for offline use. " +
              "Open it once while online, then it'll be available here.</p>" +
              "<p><a style='color:#a5b4fc' href='/library'>Go to your library</a></p></body>",
              { status: 200, headers: { "Content-Type": "text/html; charset=utf-8" } })
        }),
    )
  }
})
