// "The fandoms you read" — the list the signup form offers to pick and then
// promises something for: "We'll prioritize these ... Change anytime in
// Settings."
//
// It is stored as the comma-joined slug string signup() writes, and it is now
// editable from Settings — so it crosses the wire twice, once as that string
// and once as the list a checkbox picker holds. Two shapes of one value is
// exactly where a round trip silently loses an entry, so both directions live
// here and are tested rather than being a `.split(",")` in each caller.
//
// Parse mirrors the server (api/follows.py::_your_fandoms) on purpose: blanks
// dropped, duplicates collapsed in first-seen order, capped. A client that
// parsed more generously than the server would render a chip the server has
// already decided to drop, and one that parsed less would hide a fandom the
// reader picked.

/** Matches the server's cap. Both numbers being wrong together is the failure
 *  mode; changing one means changing the other. */
export const MAX_YOUR_FANDOMS = 24

/** The stored string (or an array, if a client ever writes one) → the list of
 *  slugs in the order the reader chose them. Never throws: this reads a value
 *  out of the account, and a corrupt one must not blank the page. */
export function parseFandomSlugs(raw: unknown): string[] {
  const parts: string[] = Array.isArray(raw)
    ? raw.map(v => String(v))
    : typeof raw === "string"
      ? raw.split(",")
      : []
  const out: string[] = []
  const seen = new Set<string>()
  for (const p of parts) {
    const slug = p.trim()
    if (!slug || seen.has(slug)) continue
    seen.add(slug)
    out.push(slug)
    if (out.length >= MAX_YOUR_FANDOMS) break
  }
  return out
}

/** The list → the string the API stores. Order is the reader's, so it is kept
 *  exactly: the front door's strip is in that order too. */
export function joinFandomSlugs(slugs: string[]): string {
  return slugs.map(s => s.trim()).filter(Boolean).join(",")
}
