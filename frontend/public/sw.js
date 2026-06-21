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

const CACHE = "ficatlas-shell-v1"
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
          const copy = res.clone()
          caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => {})
          return res
        })
        .catch(async () => {
          const cached = await caches.match(request)
          if (cached) return cached
          // Fall back to the library shell so the user lands somewhere usable.
          return (await caches.match("/library")) || (await caches.match("/")) ||
            new Response("Offline", { status: 503, headers: { "Content-Type": "text/plain" } })
        }),
    )
  }
})
