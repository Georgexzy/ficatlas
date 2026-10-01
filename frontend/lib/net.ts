/**
 * Every request this app makes must be able to give up.
 *
 * ── WHY ────────────────────────────────────────────────────────────────────
 *
 * A connection that is dead but ASSOCIATED — wifi with no route, a captive
 * portal, one bar of cell that cannot carry data — does not fail a fetch. It
 * hangs, holding the socket for as long as the platform's own timeout allows,
 * which on a phone is minutes. A browser allows about six connections per host.
 *
 * So six un-timed background requests are enough to wedge the whole origin, and
 * a page that issues a handful per navigation reaches six almost at once.
 * Measured 2026-09-28, reading saved chapters one after another on such a
 * connection, with the text sitting in IndexedDB the whole time:
 *
 *     chapter 1    0.3s
 *     chapter 2    never
 *     chapter 3    never      ... and every chapter after it
 *
 * The first page worked, which is why every previous fix looked like it had
 * worked: a single-page test cannot see this. What exhausted the pool was
 * per-navigation background traffic nobody was waiting on — the pageview
 * beacon, the follow count, two router prefetches — each one a promise that
 * would never settle and a socket that would never be released.
 *
 * ── THE RULE ───────────────────────────────────────────────────────────────
 *
 * A timeout that stops WAITING is not enough; it must stop the REQUEST. An
 * AbortController is the only thing that frees the socket — clearing a timer
 * and moving on leaves the fetch running and the connection held, which is the
 * bug above wearing the disguise of a fix.
 *
 * Nothing here is about the reader's patience. It is about not spending a
 * scarce, shared resource on a connection that has already failed.
 */

/** Background traffic: analytics, counts, prefetches. Nobody is waiting on any
 *  of it, so it gets little room and its failure is never reported. */
export const BACKGROUND_TIMEOUT_MS = 5_000

/** Traffic a reader IS waiting on: a search, a story, a list they asked for.
 *
 *  `lib/errors.ts`'s `fetchOrFail` defaults to this same number, and it imports
 *  it from here rather than repeating it — because a request that must give up
 *  needs a timeout chosen from what the caller is doing, and two lists of those
 *  choices would drift, and the one that drifts LAXER is the bug.
 */
export const USER_TIMEOUT_MS = 45_000

/** A deliberate bulk download, where the caller retries and honours Retry-After.
 *
 *  `lib/offline.ts` waits rather than abandoning when the server asks it to slow
 *  down, which is the correct answer to a rate limit and the reason this cannot
 *  share USER_TIMEOUT_MS: a long chapter over a slow connection is a success,
 *  not a stall, and cutting it off would throw away minutes of downloading.
 */
export const DOWNLOAD_TIMEOUT_MS = 120_000

/**
 * fetch() that actually gives up — and actually aborts.
 *
 * Rejects on timeout with an AbortError, exactly as an explicit abort would, so
 * callers need no special case for it.
 */
export function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit = {},
  ms: number = BACKGROUND_TIMEOUT_MS,
): Promise<Response> {
  const ctl = new AbortController()
  // A caller may have its own signal (a React cleanup, say). Honour both.
  const outer = init.signal
  if (outer) {
    if (outer.aborted) ctl.abort()
    else outer.addEventListener("abort", () => ctl.abort(), { once: true })
  }
  const timer = setTimeout(() => ctl.abort(), ms)
  return fetch(input, { ...init, signal: ctl.signal })
    .finally(() => clearTimeout(timer))
}

/**
 * Fire-and-forget, for traffic whose result nobody reads.
 *
 * The failure is swallowed deliberately — a beacon that logs an error on a bad
 * connection produces noise on exactly the screens that are already struggling
 * — but the socket is always released.
 */
export function fetchBackground(
  input: RequestInfo | URL,
  init: RequestInit = {},
  ms: number = BACKGROUND_TIMEOUT_MS,
): void {
  fetchWithTimeout(input, init, ms).catch(() => {})
}
