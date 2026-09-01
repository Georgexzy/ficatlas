/**
 * Whether to tell the reader this index is missing works from a series, and
 * what that sentence may honestly claim.
 *
 * Lives here rather than inline in the page because every part of it was wrong
 * once, in ways that only show up on particular rows:
 *
 *  - It claimed "the numbering is the author's own" for every series, including
 *    the 97,914 that FicAtlas grouped itself — directly contradicting the
 *    provenance paragraph above it, which says we guessed and may be wrong.
 *  - It computed gaps over the main run only, while positions are assigned
 *    across the whole series including companions. A complete series whose
 *    third work is a companion reported "there are gaps" immediately above the
 *    companion in question.
 *  - `max - min + 1 !== length` also fires on DUPLICATE positions, which
 *    series_works permits: it is unique on (series_id, story_id), not on
 *    position.
 */

export type SeriesSource = "explicit" | "stated" | "sequel" | "inferred" | "summary" | string

export interface NoteWork {
  position?: number | null
}

export interface SeriesNote {
  /** How the numbering may be attributed. */
  whose: string
  /** True when the list starts above 1. */
  missingBefore: boolean
  /** True when a number inside the run is absent. */
  gaps: boolean
  lowest: number
  /** Only an archive series can promise the rest are on the archive. */
  canPointAtArchive: boolean
}

/**
 * `null` when no note should be shown at all — either nothing is missing, or
 * the numbering is ours and a "gap" would say nothing about the archive.
 */
export function seriesNote(source: SeriesSource, works: NoteWork[]): SeriesNote | null {
  // An inferred, sequel- or summary-derived grouping is ordered by publication
  // date by FicAtlas. A gap there means our own cue parser missed a number, not
  // that the archive holds something we lack — so there is nothing truthful to
  // say and the provenance paragraph has already said we guessed.
  if (source !== "explicit" && source !== "stated") return null

  const pos = [...new Set(
    works.map(w => w.position).filter((n): n is number => typeof n === "number")
  )]
  if (pos.length === 0) return null

  const lowest = Math.min(...pos)
  const missingBefore = lowest > 1
  const gaps = pos.length > 1 && (Math.max(...pos) - lowest + 1) !== pos.length
  if (!missingBefore && !gaps) return null

  return {
    whose: source === "explicit"
      ? "The numbering is the author's own"
      : "The numbering is the author's, taken from what they wrote in their summaries",
    missingBefore,
    gaps,
    lowest,
    // Only an explicit series came from the archive's own series field, so only
    // there is "the rest are on AO3" a fact rather than a hope.
    canPointAtArchive: source === "explicit",
  }
}
