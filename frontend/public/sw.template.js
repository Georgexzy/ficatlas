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

// A NETWORK THAT IS NOT ANSWERING MUST NOT BE ABLE TO BLOCK A CACHED RESPONSE.
//
// Everything below used to call bare `fetch()`. On a connection that is dead
// but ASSOCIATED — wifi with no route, a captive portal, one bar of cell that
// cannot carry data — fetch() does not reject. It hangs, for as long as the
// platform allows, which on a phone can be a minute or more. So every
// "network, fall back to cache" path in this file was really "network, or
// nothing, for a very long time", and the cached copy sat unreachable the whole
// while. This is the same fault the app had in `navigator.onLine` form; see
// lib/localFirst.ts for the measurement (a saved chapter took 21 seconds).
//
// The timeout is deliberately short. It is not a request budget — it is how
// long we are willing to make somebody wait before showing them what we already
// have, and past about a second and a half a reader has decided the app is
// broken. Where nothing is cached the caller passes a longer one, because there
// the network is the only hope and giving up early helps nobody.
const NET_TIMEOUT_MS = 1500

// It must ABORT, not merely stop waiting. Clearing a timer and moving on leaves
// the fetch running and the SOCKET HELD, and a browser allows about six
// connections per host — so a worker that abandons one request per navigation
// wedges the whole origin within a few pages. Measured before this was an
// abort: reading saved chapters one after another on a dead-but-associated
// connection gave chapter 1 in 0.3s and then nothing, ever, for every chapter
// after it. A single-page test cannot see that, which is why it survived so
// long.
function fetchWithin(request, ms) {
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), ms)
  return fetch(request, { signal: ctl.signal })
    .finally(() => clearTimeout(timer))
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
          // Longer than NET_TIMEOUT_MS: nothing is cached for this URL, so
          // there is nothing better to fall back to and giving up early only
          // turns a slow asset into a missing one.
          const res = await fetchWithin(request, 10_000)
          if (res && res.status === 200) cache.put(request, res.clone()).catch(() => {})
          return res
        } catch {
          return new Response("", { status: 504, statusText: "offline" })
        }
      }),
    )
    return
  }

  // Other /_next/* (RSC/data) → network, fall back to cache — but BOUNDED.
  // Unbounded, a hung connection made this a promise that never settled, and
  // the app's boot waited on it behind a blank page.
  if (url.pathname.startsWith("/_next/")) {
    event.respondWith(
      fetchWithin(request, NET_TIMEOUT_MS)
        .catch(() => caches.match(request))
        .then((r) => r || new Response("", { status: 504, statusText: "offline" })),
    )
    return
  }

  // Navigations → CACHE FIRST, then revalidate in the background.
  //
  // This was network-first with no timeout, and that is why opening the app with
  // no connection showed nothing. With the radio truly off, fetch() rejects
  // quickly and the fallback runs — but a phone that is ASSOCIATED AND DEAD is
  // the common case: wifi with no route, a captive portal, a cell radio holding
  // a bar it cannot use. There fetch() does not reject, it hangs, for as long as
  // the platform's timeout allows. The cached shell was sitting right there the
  // whole time and nothing was allowed to serve it.
  //
  // It also explains the shape of the complaint — that offline reading only
  // worked if the app had been left open. A page already on screen performs no
  // navigation, so it never hits this path.
  //
  // Cache-first is the right default for an app shell rather than a compromise:
  // the shell is versioned by the service worker, so freshness comes from the
  // update cycle, not from re-fetching the same HTML on every launch. The
  // background revalidate keeps the copy current for next time, and a deploy
  // still lands because sw.js is served no-cache and a new worker replaces the
  // whole cache.
  if (request.mode === "navigate") {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE)
      const cached = (await cache.match(request)) ||
                     (await cache.match(url.origin + url.pathname))
      if (cached) {
        // Refresh in the background; the reader is not made to wait for it.
        event.waitUntil((async () => {
          try {
            // Bounded too. Nobody is waiting on this, but an unsettled
            // promise inside waitUntil keeps the worker alive indefinitely.
            const fresh = await fetchWithin(request, 10_000)
            if (fresh && fresh.ok) {
              await cache.put(url.origin + url.pathname, fresh.clone())
              await cache.put(request, fresh)
            }
          } catch { /* offline: the cached copy stands */ }
        })())
        return cached
      }
      // Nothing cached for THIS url — but for a story route there is always a
      // shell that can render it, so the network is not the only option and
      // must not be waited on as though it were.
      //
      // This is the path every chapter takes. /story/<id>/chapter/<n> is
      // dynamic, so it is never in the cache by its own URL; the branch above
      // could not help it, and this branch called an unbounded fetch(). On a
      // hung connection the .catch() below — the one that serves the saved
      // reader shell — simply never ran.
      const budget = url.pathname.startsWith("/story/") ? NET_TIMEOUT_MS : 10_000
      return fetchWithin(request, budget)
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
      // Known shells, then a last resort.
            //
            // This page is very nearly unreachable, which is worth knowing before
            // anyone invests in it as a surface. Navigations are cache-first (see the
            // branch above) and every successful fetch stores its own URL, so any page
            // this device has requested once is already served from the cache without
            // reaching here — and "successfully fetched" includes a 404 and a 308,
            // because the handler never checks res.ok. What is left is a URL never
            // requested on this device that is also not /library or /, which on a
            // healthy install means the precache itself failed to store its pages.
            //
            // So what it says has to be true rather than reassuring. Two systems are in
            // play and they do not expire alike: a PAGE is cached as a side effect of
            // being fetched, and the next build replaces every cached page; a STORY is
            // saved deliberately into IndexedDB and survives that. The old copy said
            // "wasn't saved for offline use" — borrowing the word the app uses for
            // "Save offline", which is a different mechanism — and then promised
            // "open it once" without mentioning the rebuild that revokes it.
            //
            // The palette is the site's own. It was hardcoded #0e0e10, which is not
            // even this site's default --bg (#111010) but a different theme's, with
            // #eee against --text #ede9e0 and an indigo link against a gold accent
            // that is deliberately constant across all three papers. A worker cannot
            // read localStorage, so the reader's chosen theme is not available here;
            // prefers-color-scheme is the honest signal, and it is still a great deal
            // better than flashing a light-mode reader a black page.
return (await cache.match("/library")) ||
        (await cache.match("/")) ||
        new Response(
          "<!doctype html><meta charset=utf-8><title>Offline</title>" +
          "<style>body{font-family:system-ui,sans-serif;margin:0;padding:2rem;" +
          "background:#111010;color:#ede9e0;line-height:1.55}" +
          "h1{font-size:1.3rem;margin:0 0 .8rem}" +
          "p{margin:0 0 .8rem;max-width:34rem}" +
          "a{color:#c8a96e}" +
          "@media (prefers-color-scheme:light){body{background:#f4ecd8;color:#4a3f2f}}</style>" +
          "<body><h1>You're offline</h1>" +
          "<p>This page isn't stored on this device, so there's no copy to show.</p>" +
          "<p>Open it once while you're connected and it will be here after " +
          "that, until the next update of FicAtlas replaces the stored pages.</p>" +
          "<p>Your library, and any story you saved with &#x2913; Save offline, " +
          "keep working offline regardless.</p>" +
          "<p><a href='/library'>Go to your library</a></p></body>",
          { status: 200, headers: { "Content-Type": "text/html; charset=utf-8" } })
        })
    })())
  }
})
