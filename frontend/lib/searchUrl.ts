// The query string that goes in the ADDRESS, as opposed to the one that goes to
// the API.
//
// These are not the same thing, and conflating them is how a reader's content
// preference ended up in a link. Two parameters are removed here and nowhere
// else:
//
//   - A value equal to the page's own default carries no information. `?sort=relevance`
//     is not a search, it is the absence of one.
//   - A content preference does not belong in a shareable address at all.
//
// The second is not expressible as the first. `URL_DEFAULTS` drops a parameter
// equal to its default, and `explicit=true` is precisely the value that is NOT
// the default — so the single case worth dropping was the single case the
// mechanism preserved. That is the whole defect: a reader who had chosen to see
// adult content got a URL carrying `explicit=true`, and anyone who opened that
// link was shown that content without choosing it.
//
// Both call sites MUST produce the same string. One builds the address that
// `router.push` navigates to; the other builds the key `prefetched` is stored and
// looked up under. They are the same string by construction here, rather than by
// two loops that were once identical and had to be kept identical by hand — which
// is exactly how they drifted, and why fixing one without the other would have
// turned every hover prefetch into a request whose result nothing could claim.

/** Values the page supplies anyway, so putting them in the address says nothing. */
export const URL_DEFAULTS: Record<string, string> = {
  sites: "ao3,ffnet,fictionalley",
  match_mode: "all",
  sort: "relevance",
  page: "1",
  per_page: "20",
  include_unknown: "false",
  explicit: "false",
  crossovers: "include",
  ratings: "G,T,M,NR",
}

/**
 * Parameters that must never appear in a shareable address.
 *
 * `explicit` is tier 2 (adult and deliberately disturbing) and `include_underage`
 * is tier 1 (sexualised minors). Both are consent, and consent is a standing
 * property of the reader: it is set in Settings, stored on the device, already
 * synced to their account, and applied on arrival. In the address it becomes
 * something else — a link that opens a page of work on whoever opens it, on the
 * one screen whose URLs get pasted into public threads.
 *
 * `include_underage` is here for the same reason and more strongly: it is about
 * what a link carries rather than about taste. `OutreachPanel` already strips both
 * from every link it builds; this closes the paths that build links by other
 * means, which is every URL bar, every redirect and every hand-typed link.
 */
export const NEVER_IN_URL: ReadonlySet<string> = new Set(["explicit", "include_underage"])

/**
 * Build the address query string for a set of search parameters.
 *
 * The caller still sends the full parameter object to the API — this function
 * only decides what is written down, so nothing about the search itself changes.
 * Returns the query string WITHOUT a leading `?`.
 *
 * Generic over `object` rather than taking `Record<string, unknown>`, because the
 * thing being passed is `SearchParams`, an interface with no index signature. The
 * values are stringified anyway, so there is nothing to lose by being loose about
 * the shape and something to lose by making callers cast.
 */
export function urlQueryString<T extends object>(params: T): string {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params as Record<string, unknown>)) {
    if (v === undefined || v === null || v === "") continue
    if (NEVER_IN_URL.has(k)) continue
    if (URL_DEFAULTS[k] === String(v)) continue
    qs.set(k, String(v))
  }
  return qs.toString()
}