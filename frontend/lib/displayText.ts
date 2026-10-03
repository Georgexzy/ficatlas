// Text that is shown back to the reader, cut to a length worth reading.
//
// The search bar will hold an entire fic-finder post. The results line has
// always truncated at 44 characters for that reason, and the results state's
// hidden <h1> quotes the same text — so both take the same cut.
//
// They are one function rather than two `slice` calls because the second one is
// invisible when it drifts: the heading is visually hidden, so there is no
// layout consequence to notice and reading the JSX is the only way to see the
// disagreement. Measured at 72 characters of heading for a 60-character query
// before they were shared.

export const QUERY_CLAMP = 44

export function clampQuery(q: string): string {
  const t = q.trim()
  return t.length > QUERY_CLAMP ? t.slice(0, QUERY_CLAMP) + "…" : t
}
