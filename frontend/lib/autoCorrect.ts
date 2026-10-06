// Correcting a search that found nothing, when the correction is not a guess.
//
// Measured over 30 days of real searches: 14% returned zero works, and the
// commonest single failure was a typo the site already knew the answer to —
// "the arithmancr" ran five times for two people, each time showing "No
// stories matched" with "The Arithmancer" offered underneath, which nobody
// took. A mistyped operator ("shgip:") is the same failure one level up: the
// whole filter became text, and every word of it had to appear in a story.
//
// So when a search finds NOTHING, and only then, one of these may re-run it:
//   * a mistyped operator, one or two letters from a real one;
//   * the top spelling suggestion, when it is within a typo or two of exactly
//     what was typed — "within a typo" is what makes it a correction rather
//     than a different search. A suggestion further away stays a suggestion.
// The page says what it did and hands back the reader's own words.

import { operatorKeys } from "./queryParser"

/** Edits between two strings, a swapped pair of letters counting as ONE —
 *  the commonest typing slip ("fandmo", "teh"), which plain Levenshtein
 *  charges twice. Optimal string alignment distance. */
export function editDistance(a: string, b: string): number {
  const m = a.length, n = b.length
  if (!m) return n
  if (!n) return m
  const d: number[][] = Array.from({ length: m + 1 }, (_, i) =>
    Array.from({ length: n + 1 }, (_, j) => (i === 0 ? j : j === 0 ? i : 0)))
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
        d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1)
      }
    }
  }
  return d[m][n]
}

/** The query with mistyped operator keys fixed, or null if there were none. */
export function fixOperatorTypos(q: string): string | null {
  const keys = operatorKeys().filter(k => k.length >= 3)
  const known = new Set(operatorKeys())
  let changed = false
  const out = q.replace(/(^|\s)(-?)([A-Za-z_]{3,}):/g, (whole, sp, neg, key) => {
    const k = key.toLowerCase()
    if (known.has(k)) return whole
    // One edit for short keys, two for long ones; and the nearest must be
    // unique, or the fix is a coin toss.
    const allow = k.length >= 6 ? 2 : 1
    let best = "", bestD = 99, tie = false
    for (const cand of keys) {
      const d = editDistance(k, cand)
      if (d < bestD) { best = cand; bestD = d; tie = false }
      else if (d === bestD && cand !== best) tie = true
    }
    if (bestD > allow || tie) return whole
    changed = true
    return `${sp}${neg}${best}:`
  })
  return changed ? out : null
}

export interface SpellingSuggestion {
  reason?: string; value: string; query: string; count: number; kind?: string
}

/** The suggestion's query, if it is confidently the same search spelled right. */
export function confidentSpelling(typed: string, sug: SpellingSuggestion[] = []): string | null {
  const t = typed.trim()
  if (!t || /(^|\s)-?[A-Za-z_]+:/.test(t)) return null      // operators: not a typo fix
  const top = sug.find(s => (s.reason ?? "spelling") === "spelling")
  if (!top || !top.query || top.count < 20) return null
  const a = t.toLowerCase(), b = top.value.trim().toLowerCase()
  if (a === b) return null                                   // echoing the search back
  const allow = Math.max(1, Math.floor(b.length * 0.15))
  return editDistance(a, b) <= Math.min(allow, 3) ? top.query : null
}
