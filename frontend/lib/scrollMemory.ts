// Remember where in a search you were, so "Back" from a story returns you to
// the same results at the same height instead of dumping you at the top of a
// reloaded page.
//
// The query itself is already cached by lastSearch.ts (the Search nav item uses
// it). This complements it for the back-navigation case, where the URL is
// restored by history but the browser may not restore the scroll — the search
// page remounts and re-fetches, which on an async results render lands you back
// at the top. One entry, keyed by the exact URL, taken (read-and-cleared) once
// by the page that owns it so a stale value can never be replayed onto a
// different or fresh search.

const KEY = "ficatlas:scroll-memory"

export function saveScroll(href: string, y: number): void {
  if (typeof window === "undefined") return
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ href, y }))
  } catch { /* private mode — the feature degrades to browser-default scroll */ }
}

/** Read-and-clear the remembered scroll for this exact URL, or null. */
export function takeScroll(href: string): number | null {
  if (typeof window === "undefined") return null
  try {
    const raw = sessionStorage.getItem(KEY)
    if (!raw) return null
    const d = JSON.parse(raw)
    if (d && d.href === href && typeof d.y === "number" && d.y > 0) {
      sessionStorage.removeItem(KEY)
      return d.y
    }
    if (d && d.href !== href) sessionStorage.removeItem(KEY)
  } catch {}
  return null
}
