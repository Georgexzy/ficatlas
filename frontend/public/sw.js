// FicAtlas service worker — makes the app shell load with no network so saved
// stories are readable offline. Story *content* lives in IndexedDB (see
// lib/offline.ts); this SW only handles caching the HTML/JS/CSS shell and static
// assets, plus serving a graceful offline fallback for navigations.
//
// Strategy:
//   - Static assets (/_next/static/*, fonts, icons): cache-first (they're
//     content-hashed and immutable, so a cache hit is always correct).
//   - Navigations (HTML page loads): network-first, falling back to the cached
//     shell when offline so the SPA can boot and read from IndexedDB.
//   - API calls (/api/*): network-only. We never cache search/API responses —
//     offline reading is served from IndexedDB inside the app, not here.

const CACHE = "ficatlas-shell-v2"
const OFFLINE_URLS = ["/", "/library", "/offline"]

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(OFFLINE_URLS).catch(() => {})),
  )
  self.skipWaiting()
})

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))),
    ),
  )
  self.clients.claim()
})

self.addEventListener("fetch", (event) => {
  const { request } = event
  const url = new URL(request.url)

  // Never touch API traffic — let it hit the network (and fail naturally offline;
  // the app handles that by reading IndexedDB).
  if (url.pathname.startsWith("/api/")) return

  // Skip Next.js dev/HMR machinery — caching these breaks hot reload and can
  // serve stale chunks in dev mode. (Harmless in prod; these paths won't exist.)
  if (url.pathname.startsWith("/_next/webpack-hmr") ||
      url.pathname.includes("__nextjs") ||
      url.search.includes("hot-update")) return

  // Only handle same-origin GETs.
  if (request.method !== "GET" || url.origin !== self.location.origin) return

  // Next.js chunks and other static assets. In dev these change on rebuild, so
  // use network-first (fall back to cache only when offline) to avoid serving
  // stale JS. The cache still enables offline boot.
  if (url.pathname.startsWith("/_next/") ||
      url.pathname.match(/\.(?:js|css|woff2?|ttf|png|svg|ico|webp)$/)) {
    event.respondWith(
      fetch(request)
        .then((res) => {
          const copy = res.clone()
          caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => {})
          return res
        })
        .catch(() => caches.match(request)),
    )
    return
  }

  // Navigations (page loads) → network-first, fall back to cached shell.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((res) => {
          // Cache a clean copy keyed by pathname (no query) so a later offline
          // visit to the same route matches regardless of query string.
          const copy = res.clone()
          caches.open(CACHE).then((c) => {
            c.put(request, copy.clone()).catch(() => {})
            c.put(url.origin + url.pathname, copy).catch(() => {})
          }).catch(() => {})
          return res
        })
        .catch(async () => {
          // Offline: try exact match, then pathname-only, then the known shells.
          const cache = await caches.open(CACHE)
          return (await cache.match(request)) ||
            (await cache.match(url.origin + url.pathname)) ||
            (await cache.match("/library")) ||
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
