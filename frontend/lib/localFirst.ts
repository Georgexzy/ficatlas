/**
 * Show what this device already holds; let the network improve on it.
 *
 * ── WHY THIS EXISTS ────────────────────────────────────────────────────────
 *
 * Offline reading was reported as not working, repeatedly, across eighteen
 * commits of fixes. Measured on 2026-09-28 against a chapter already saved to
 * the device, with the network dead but ASSOCIATED — wifi with no route, a
 * captive portal, one bar of cell that cannot carry data:
 *
 *     radio genuinely off (navigator.onLine === false)   chapter shown in  <3s
 *     dead but associated (navigator.onLine === true)    chapter shown in  21s
 *
 * Twenty-one seconds, with the text sitting in IndexedDB the whole time,
 * readable in single-digit milliseconds. Nobody waits twenty-one seconds, so
 * from the reader's side the feature simply does not work — and it fails in the
 * case that is by far the most common, because a phone that is truly radio-off
 * is rarer than a phone holding a bar it cannot use.
 *
 * The cause was one line, and it was the same line in three files:
 *
 *     if (!navigator.onLine && await fromOffline()) return
 *
 * `navigator.onLine` reports whether the machine has a network INTERFACE, not
 * whether packets arrive. On a dead-but-associated connection it is `true`. So
 * the shortcut to the saved copy never fired, two doomed fetches were raced
 * instead, and the saved copy was reached only after a 20-second abort timer
 * gave up. The old comment claimed this was the case where onLine "is most
 * likely to be right". It is exactly the case where it is wrong.
 *
 * ── THE INVERSION ──────────────────────────────────────────────────────────
 *
 * The old model was *network first, local copy as the fallback*. For a reader
 * who deliberately downloaded a story that is backwards: they told us to keep
 * it precisely so it would not depend on the network.
 *
 * So: never await the network before showing what we hold. The local read runs
 * unconditionally and immediately, and the network runs beside it as an
 * upgrade that may or may not arrive. A timeout is no longer what rescues the
 * reader — it only stops a refresh indicator spinning — which is why the
 * reader's experience no longer depends on choosing its value correctly.
 *
 * `navigator.onLine` does not appear in this file. It is a fine hint for what
 * to SAY ("you appear to be offline") and was never fit to decide what to DO.
 */

export interface LocalFirstOptions<T> {
  /** Read the copy on this device. Resolve null when there is none. Must not
   *  throw for "nothing here" — that is a null, not a failure. */
  local: () => Promise<T | null>
  /** Ask the network. Rejects on failure; honours the signal. */
  remote: (signal: AbortSignal) => Promise<T>
  /** The local copy, the moment it is read. May be followed by `onRemote`. */
  onLocal: (value: T) => void
  /** A fresher copy from the network. Never called after cancellation. */
  onRemote: (value: T) => void
  /** Only when there is nothing to show AT ALL — no local copy and no network.
   *  Never called while the reader has something on screen. */
  onFailure: (error: unknown) => void
  /** How long the network gets when we have nothing else to show. */
  patienceMs?: number
  /** How long it gets when the reader is already reading. Longer is harmless
   *  here: nobody is waiting on it. */
  graceMs?: number
  /** True once the caller has navigated away. Checked before every callback. */
  isCancelled?: () => boolean
}

/** How long a reader with NOTHING to look at waits before being shown an error
 *  they can act on.
 *
 *  It was 20 seconds, which was never measured against anything. Measured
 *  2026-09-28, a chapter fetch takes **12-54ms** from the app's own network and
 *  **130-510ms** through the public edge — so 20s is two orders of magnitude
 *  past what a healthy request costs, and every second of it is spent on a
 *  connection that has already failed. 8s is ~15x the worst real figure, which
 *  is generous for a bad phone connection, and it gets the error screen — which
 *  offers Retry and lists the chapters this device DOES hold — in front of the
 *  reader while they still care. */
export const DEFAULT_PATIENCE_MS = 8_000
/** How long the network gets when the reader is ALREADY READING.
 *
 *  This was 30s, on the reasoning that "nobody is waiting on it, so longer is
 *  harmless". That reasoning was wrong, and the measurement is why: a browser
 *  allows about six connections per host, so an upgrade request nobody is
 *  waiting on still holds a slot that the NEXT chapter needs. Each chapter view
 *  opens two of them (the story and the chapter), so at 30s a reader turning
 *  pages on a dead connection ran the origin out of sockets within three pages
 *  — and the symptom was the saved text failing to appear, which looks nothing
 *  like an over-long timeout.
 *
 *  5s: long enough to pick up a genuine update on a working connection (a
 *  chapter costs 130-510ms through the edge), short enough that a dead one
 *  cannot accumulate. */
export const DEFAULT_GRACE_MS = 5_000

export async function localFirst<T>(opts: LocalFirstOptions<T>): Promise<void> {
  const {
    local, remote, onLocal, onRemote, onFailure,
    patienceMs = DEFAULT_PATIENCE_MS,
    graceMs = DEFAULT_GRACE_MS,
    isCancelled = () => false,
  } = opts

  let haveLocal = false

  // 1. What we hold, first and unconditionally. This is a local database read:
  //    it costs single-digit milliseconds and cannot be blocked by a network
  //    that is not answering. Nothing is awaited ahead of it.
  //
  //    A throw here is treated as "nothing held" rather than propagated: a
  //    corrupt or evicted store must not cost the reader the network copy too.
  let localValue: T | null = null
  try {
    localValue = await local()
  } catch {
    localValue = null
  }
  if (isCancelled()) return
  if (localValue != null) {
    haveLocal = true
    onLocal(localValue)
  }

  // 2. The network, beside it. Its own timeout, because a dead-but-associated
  //    connection does not reject — it hangs, and an un-timed fetch on such a
  //    connection is a promise that never settles.
  const ctl = new AbortController()
  const budget = haveLocal ? graceMs : patienceMs
  const timer = setTimeout(() => ctl.abort(), budget)
  try {
    const fresh = await remote(ctl.signal)
    if (isCancelled()) return
    onRemote(fresh)
  } catch (e) {
    if (isCancelled()) return
    // Silence when the reader is already reading. A work saved to this device
    // is not "unavailable" because the server could not be reached — telling
    // them so, over text they can see, is the complaint this whole file exists
    // to answer.
    if (!haveLocal) onFailure(e)
  } finally {
    clearTimeout(timer)
  }
}
