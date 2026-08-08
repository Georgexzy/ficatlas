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
// One entry, keyed by the exact URL. Restore clears it ONLY on a successful
// scroll, so a failed attempt (page still fetching/short) never throws the
// position away before a later attempt can use it.
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

export function saveScroll(href: string, y: number): void {
  if (typeof window === "undefined") return
  try { sessionStorage.setItem(KEY, JSON.stringify({ href, y })) }
  catch { /* private mode — the feature degrades to browser-default scroll */ }
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
  const attempt = () => {
    if (document.documentElement.scrollHeight > d.y) {
      window.scrollTo({ top: d.y, left: 0, behavior: "instant" })
      try { sessionStorage.removeItem(KEY) } catch {}
      return
    }
    // ~2s window; the on-demand search restore (results-loaded) is the backstop
    // that retries after a slow fetch.
    if (++tries < 120) requestAnimationFrame(attempt)
  }
  attempt()
}

if (typeof window !== "undefined") {
  window.addEventListener("popstate", () => {
    restoreScroll(window.location.pathname + window.location.search)
  })
}
