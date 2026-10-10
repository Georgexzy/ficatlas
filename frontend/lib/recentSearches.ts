const KEY = "ficatlas:recent-searches"
export const MAX_RECENT_SEARCHES = 20

/** Read the recent query log without letting damaged storage break search. */
export function loadRecentSearches(limit = MAX_RECENT_SEARCHES): string[] {
  if (typeof window === "undefined") return []
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "[]")
    return Array.isArray(raw)
      ? raw.filter((q): q is string => typeof q === "string" && q.trim().length > 0)
          .slice(0, limit)
      : []
  } catch { return [] }
}

/** Move a completed search to the top, keeping the newest twenty. */
export function rememberRecentSearch(query: string): string[] {
  if (typeof window === "undefined") return []
  const q = query.trim()
  if (!q) return loadRecentSearches()
  const next = [q, ...loadRecentSearches().filter(item => item !== q)]
    .slice(0, MAX_RECENT_SEARCHES)
  try { localStorage.setItem(KEY, JSON.stringify(next)) } catch {}
  return next
}

/** Remove one exact recent query while leaving the rest of the history intact. */
export function removeRecentSearch(query: string): string[] {
  const next = loadRecentSearches(MAX_RECENT_SEARCHES).filter(item => item !== query)
  try {
    if (typeof window !== "undefined") localStorage.setItem(KEY, JSON.stringify(next))
  } catch {}
  return next
}

export function clearRecentSearches(): void {
  try {
    if (typeof window !== "undefined") localStorage.setItem(KEY, "[]")
  } catch {}
}
