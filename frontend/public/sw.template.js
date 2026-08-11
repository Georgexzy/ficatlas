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

// Derived from the build stamp by gen-sw-precache.js, so it changes on every
// build. `activate` deletes every cache whose name differs, which is what
// evicts the previous shell.
//
// This used to be a hand-maintained "v8". Bumping it was a manual step on any
// change to the precache set or fetch strategy, and forgetting meant the old
// cache was never evicted — entries for superseded hashed assets accumulated
// build after build. Tying it to the build removes the step and the mistake.
const CACHE = "__CACHE_VERSION__"
const PRECACHE = __PRECACHE_MANIFEST__

// The entries without which the app cannot start at all: the shell HTML plus
// the framework, webpack runtime and app-entry chunks. Everything else can be
// missing and the app still boots and fetches it later; these cannot.
//
// Matched by prefix because the chunk names are content-hashed per build.
const ESSENTIAL = ["/", "/_next/static/chunks/webpack-", "/_next/static/chunks/main-app-",
                   "/_next/static/chunks/framework-", "/_next/static/chunks/app/layout-"]

function isEssential(url) {
  return ESSENTIAL.some((p) => url === p || url.startsWith(p))
}

// A precache that half-worked used to destroy a working one.
//
// The old install swallowed every failure — cache.add(u).catch(() => {}) — then
// called skipWaiting() outside waitUntil, so it activated whatever happened.
// Activate then deleted EVERY cache whose name differed from the new one.
//
// On a phone with a weak or dropping connection that is a disaster: the new
// worker installs, most of its fetches fail silently, it activates anyway, and
// it deletes the old cache that was working perfectly. The next time the app is
// opened with no connection, nothing loads at all — the reported symptom, and
// the reason it appeared "randomly": it needs a bad connection at exactly the
// moment a new build is picked up.
//
// So: failures are counted rather than ignored, and if any ESSENTIAL entry did
// not make it, install FAILS. A failed install leaves the previous worker in
// control with its cache intact, and the browser retries later. A stale-but
// working offline app beats a current-but-empty one.
self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE)
    const missing = []
    await Promise.all(PRECACHE.map(async (u) => {
      try {
        await cache.add(u)
      } catch {
        missing.push(u)
      }
    }))
    const criticalMissing = missing.filter(isEssential)
    if (criticalMissing.length) {
      // Leave nothing half-built behind for activate to promote.
      await caches.delete(CACHE)
      throw new Error("precache incomplete: " + criticalMissing.join(", "))
    }
    // Only take over once there is a complete cache to take over with.
    await self.skipWaiting()
  })())
})

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    // Verified before anything is deleted. Reaching activate means install
    // succeeded, but the cache is shared mutable state and a quota eviction
    // between the two would otherwise leave the reader with nothing.
    const cache = await caches.open(CACHE)
    const holds = await Promise.all(ESSENTIAL.map(async (p) => {
      if (await cache.match(p)) return true
      const keys = await cache.keys()
      return keys.some((r) => new URL(r.url).pathname.startsWith(p))
    }))
    if (holds.every(Boolean)) {
      const keys = await caches.keys()
      await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    }
    await self.clients.claim()
  })())
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
