// One way of leaving a page, used everywhere it matters.
//
// Next's client router is not a navigation, it is a request. `<Link>` and
// `router.push` both fetch an RSC payload before anything on screen changes, so
// they inherit every failure mode of the network:
//
//   * offline, the fetch rejects and the router gives up silently — the button
//     appears dead;
//   * with the backend merely slow or wedged, the fetch neither resolves nor
//     rejects for a long time, and the button appears dead in exactly the same
//     way, which is the harder case because nothing is technically wrong;
//   * a service worker that holds a cached copy of the destination is never
//     consulted, because no document request is ever made.
//
// A real document navigation has none of those problems: the service worker can
// answer it from cache, and the browser shows progress. It is slower when
// everything is healthy, which is why it is the fallback and not the default.
//
// So: try the client router, and if the URL has not actually changed shortly
// after, do the real thing. The user gets SPA speed when the site is well and a
// working button when it is not.

/** How long to let the client router prove it can navigate before forcing a
 *  document request. Comfortably longer than a healthy RSC round trip, short
 *  enough that a dead button is not what the reader experiences. */
export const ROUTER_PATIENCE_MS = 1200

/** The same idea offline, but the router has far less to prove.
 *
 *  Offline used to skip the router entirely and force a document request. That
 *  is correct for a route the browser has never seen, and badly wrong for one
 *  it has: a document navigation offline means the service worker serves the
 *  shell and the whole app boots again — parse, execute, hydrate, re-read
 *  IndexedDB — for every chapter. Turning pages in a saved story rebooted the
 *  application each time, which is what "ages to go to the next chapter"
 *  actually was.
 *
 *  A route already in the router cache needs no network, so the client
 *  transition works offline and is instant. The reader prefetches its
 *  neighbours, so next/previous are nearly always cached. This is the wait
 *  before giving up on that: long enough for a cache hit to render, short
 *  enough that an uncached route does not feel stalled before the reload. */
export const OFFLINE_ROUTER_PATIENCE_MS = 400

type PushFn = (href: string) => void

export function navigateTo(push: PushFn, href: string): void {
  if (typeof window === "undefined") return

  push(href)

  // navigator.onLine is only trustworthy when false (see lib/errors.ts), which
  // is the direction that matters here: a confident "offline" shortens the wait
  // before the document-navigation fallback, rather than skipping the attempt.
  const patience = navigator.onLine
    ? ROUTER_PATIENCE_MS
    : OFFLINE_ROUTER_PATIENCE_MS

  const target = href.split(/[?#]/)[0]
  window.setTimeout(() => {
    // Compare paths, not full URLs: the router legitimately keeps query strings
    // and hashes that the caller did not ask for, and a mismatch there is not a
    // failed navigation.
    if (window.location.pathname !== target) window.location.href = href
  }, patience)
}
