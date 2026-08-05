// Where "Search" takes you once you already have a search.
//
// Navigating between pages was fast — 25ms to Settings, 26ms to About — and it
// felt slow anyway, because leaving search and coming back threw the search
// away. The Search nav item and the tab bar both pointed at "/", which renders
// an empty results page, so the only way back to what you were reading was the
// browser's Back button. On a phone installed as a PWA there isn't one.
//
// So the cost was never the navigation. It was retyping a query and re-picking
// four filters, which is several seconds of work and reads as the site being
// slow. Remembering the last search makes the trip back free.
//
// sessionStorage, not localStorage: this is "where I was just now", and it
// should not still be there tomorrow. Opening a fresh tab starts at a clean
// search, which is what the logo does too.
const KEY = "ficatlas:last-search"

/** Record a search worth returning to. Ignores empty ones — an unfiltered "/"
 *  is not somewhere anybody needs bringing back to. */
export function rememberSearch(search: string): void {
  if (typeof window === "undefined") return
  const qs = search.startsWith("?") ? search.slice(1) : search
  const p = new URLSearchParams(qs)
  // page/per_page alone mean nothing was actually asked for.
  const meaningful = [...p.keys()].some(
    k => !["page", "per_page", "sort", "match_mode", "explicit"].includes(k))
  try {
    if (meaningful) sessionStorage.setItem(KEY, "/?" + qs)
    else sessionStorage.removeItem(KEY)
  } catch { /* private mode — the feature degrades to plain "/" */ }
}

/** The href "Search" should use, or "/" when there is nothing to return to. */
export function lastSearchHref(): string {
  if (typeof window === "undefined") return "/"
  try {
    return sessionStorage.getItem(KEY) || "/"
  } catch {
    return "/"
  }
}
