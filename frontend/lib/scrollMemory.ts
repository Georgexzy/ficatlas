// Remember where in a search you were, so "Back" from a story returns you to
// the same results at the same height instead of dumping you at the top of a
// reloaded page.
//
// The query itself is already cached by lastSearch.ts (the Search nav item uses
// it). This complements it for the back-navigation case, where the URL is
// restored by history but the browser may not restore the scroll — the search
// page remounts and re-fetches, which on an async results render lands you back
// at the top.
//
// One entry, keyed by the exact URL, holding the last known position for that
// search. A restore never clears it — see the note in restoreScroll — and a
// failed attempt never overwrites it, so neither a slow fetch nor a page that
// is still too short can throw the position away.
//
// A popstate listener is registered at MODULE scope, not inside a component:
// back/forward can restore the search page from Next's router cache WITHOUT
// remounting it, in which case the page's own effects never run again and a
// component-mounted listener is gone. The module lives for the tab's lifetime,
// so it always fires. It is a no-op on any page that never saved a position,
// because entries are keyed to an exact URL — only the search page saves.

const KEY = "ficatlas:scroll-memory"

function read(): { href: string; y: number } | null {
  if (typeof window === "undefined") return null
  try {
    const raw = sessionStorage.getItem(KEY)
    if (!raw) return null
    const d = JSON.parse(raw)
    return d && typeof d.y === "number" ? { href: d.href, y: d.y } : null
  } catch { return null }
}

// How many restores are mid-flight. A restore is not instant — it waits for the
// results to render and the document to grow — and the page's scroll listener is
// live throughout, so the browser's own back-navigation scroll (which lands
// wherever it likes; measured at the very bottom of the page) was being recorded
// as the reader's position and overwriting the one being restored TO. Saving is
// suppressed while a restore is pending, and only while one is: this is a
// counter rather than a boolean so two overlapping attempts cannot leave it
// stuck on, and it is released when the attempt gives up as well as when it
// succeeds.
let restoring = 0

export function saveScroll(href: string, y: number): void {
  if (typeof window === "undefined") return
  if (restoring > 0) return
  try { sessionStorage.setItem(KEY, JSON.stringify({ href, y })) }
  catch { /* private mode — the feature degrades to browser-default scroll */ }
}

/** Forget the position for this URL: the reader is back at the top of it.
 *
 * Without this the y>0 guard in the page's capture meant scrolling back to the
 * top merely declined to SAVE, leaving the old deep position in place — so
 * Back returned the reader to where they had been two visits ago rather than
 * to the top they had chosen. Refuses to run mid-restore, where a transient
 * y of 0 is the restore itself rather than the reader. */
export function clearScroll(href: string): void {
  if (typeof window === "undefined") return
  if (restoring > 0) return
  try {
    const d = read()
    if (d && d.href === href) sessionStorage.removeItem(KEY)
  } catch {}
}

/** Read-and-clear the remembered scroll for this exact URL, or null. */
export function takeScroll(href: string): number | null {
  if (typeof window === "undefined") return null
  try {
    const d = read()
    if (d && d.href === href && d.y > 0) { sessionStorage.removeItem(KEY); return d.y }
    if (d && d.href !== href) sessionStorage.removeItem(KEY)
  } catch {}
  return null
}

// Scroll to the saved position for this URL once the page is tall enough,
// retrying across frames so a back-navigation that is still fetching results
// can still land correctly once they render. Clears the entry only on success,
// so a too-early attempt does not discard it for the one that runs later.
export function restoreScroll(href: string): void {
  if (typeof window === "undefined") return
  const d = read()
  if (!d || d.href !== href || d.y <= 0) return
  let tries = 0
  restoring++
  let done = false
  const finish = () => { if (!done) { done = true; restoring-- } }
  const attempt = () => {
    // Can the document actually REACH that position? The test used to be
    // `scrollHeight > d.y`, which is a different and much weaker question, and
    // it was asked at the worst possible moment: on a back-navigation this runs
    // while the page being LEFT is still the one laid out. Measured — a 1,882px
    // story page passed the test for y=1,400, the scroll was clamped to the 982
    // that document could reach, and the entry was then cleared as though the
    // restore had worked. The reader ended up wherever the browser felt like,
    // with nothing left to try again from. What matters is the SCROLLABLE
    // height: everything below the fold.
    const reachable = document.documentElement.scrollHeight - window.innerHeight
    if (reachable >= d.y) {
      window.scrollTo({ top: d.y, left: 0, behavior: "instant" })
      // Landed? A couple of pixels of slack, because a browser may settle on a
      // subpixel boundary.
      //
      // The entry is deliberately NOT cleared here. A successful restore used
      // to consume it, and that lost the position outright whenever the reader
      // was already at the remembered height: scrollTo to where you already are
      // fires no scroll event, so the page's capture never ran and never wrote
      // it back. Measured — two back-navigations in three came up at the top of
      // the results afterwards. The entry is keyed to an exact URL and only
      // ever read for that URL, so keeping it costs nothing and makes this the
      // last known position for that search rather than a one-shot token.
      if (Math.abs(window.scrollY - d.y) <= 2) {
        finish()
        return
      }
    }
    // ~2s window; the on-demand search restore (results-loaded) is the backstop
    // that retries after a slow fetch.
    if (++tries < 120) requestAnimationFrame(attempt)
    else finish()   // give up, and let the page record positions again
  }
  attempt()
}

if (typeof window !== "undefined") {
  window.addEventListener("popstate", () => {
    restoreScroll(window.location.pathname + window.location.search)
  })
}
