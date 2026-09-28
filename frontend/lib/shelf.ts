/**
 * The library shelf: one list, assembled from four sources.
 *
 * Bookmarks, Reading, Offline and Your imports were four sibling tabs in
 * /library, and they are four VIEWS OF ONE SET rather than four sets. Measured
 * on the only account that has a shelf: 32 distinct works listed 43 times —
 * six of the nine works in progress are also imports, all four downloads are
 * also in progress, and the single bookmark is in all three at once.
 *
 * So a reader who bookmarks a story, reads three chapters and saves it for the
 * train had to remember which of four tabs it was filed under, and the honest
 * answer was "all of them". That is what "ungainly and hard to navigate"
 * looked like in the data.
 *
 * This file is the merge, kept pure and out of the component so it can be
 * tested against real exports — see shelf.test.ts, which asserts the counts
 * above rather than restating them in a comment.
 */

export interface ShelfBookmark { id: string; title: string; author?: string; site?: string; savedAt?: string }
export interface ShelfProgress { chapter: number; at: string; title: string }
export interface ShelfOffline { id: string; title: string; author?: string; savedAt?: string | number
                                word_count?: number; chapter_count?: number }
export interface ShelfImport { id: string; title: string; author?: string; site?: string
                               word_count?: number; chapter_count?: number; added_at?: string | null }

export interface ShelfItem {
  id: string
  title: string
  author?: string
  site?: string
  word_count?: number
  chapter_count?: number
  /** When it joined the shelf, by whichever route put it there first. */
  added: number
  bookmark?: ShelfBookmark
  progress?: ShelfProgress
  offline?: ShelfOffline
  imported?: ShelfImport
}

export type ShelfFilter = "all" | "reading" | "bookmarks" | "offline" | "mine"

export const shelfHas = (it: ShelfItem, f: ShelfFilter): boolean =>
  f === "all"       ? true
: f === "reading"   ? !!it.progress
: f === "bookmarks" ? !!it.bookmark
: f === "offline"   ? !!it.offline
: /* mine */          !!it.imported

const ts = (v?: string | number | null): number =>
  v == null ? 0 : typeof v === "number" ? v : (Date.parse(v) || 0)

/**
 * Merge the four sources into one list, keyed on the story id — which every
 * source already stores, so this is exact and never a title match. (The index
 * holds five works called "Manacled"; merging on titles would fuse them.)
 */
export function buildShelf(src: {
  bookmarks?: ShelfBookmark[]
  progress?: Record<string, ShelfProgress>
  offline?: ShelfOffline[]
  imports?: ShelfImport[]
}): ShelfItem[] {
  const by = new Map<string, ShelfItem>()
  const row = (id: string, title: string): ShelfItem => {
    let it = by.get(id)
    if (!it) { it = { id, title, added: 0 }; by.set(id, it) }
    // Whichever source has a title wins over one that has none; they agree in
    // practice, and a blank row is worse than an arbitrary tie-break.
    if (!it.title && title) it.title = title
    return it
  }
  // Imports first: the fullest record (author, site, length, chapters), so the
  // later sources fill gaps rather than overwrite what is already known.
  for (const m of src.imports ?? []) {
    const it = row(m.id, m.title)
    it.imported = m
    it.author ??= m.author; it.site ??= m.site
    it.word_count ??= m.word_count; it.chapter_count ??= m.chapter_count
    it.added = Math.max(it.added, ts(m.added_at))
  }
  for (const b of src.bookmarks ?? []) {
    const it = row(b.id, b.title)
    it.bookmark = b
    it.author ??= b.author; it.site ??= b.site
    it.added = Math.max(it.added, ts(b.savedAt))
  }
  for (const [id, pr] of Object.entries(src.progress ?? {})) {
    row(id, pr.title).progress = pr
  }
  for (const o of src.offline ?? []) {
    const it = row(o.id, o.title)
    it.offline = o
    it.author ??= o.author
    it.word_count ??= o.word_count; it.chapter_count ??= o.chapter_count
    // Offline records stamp savedAt as a number in some versions and an ISO
    // string in others, and Date.parse of a number is NaN.
    it.added = Math.max(it.added, ts(o.savedAt))
  }
  return [...by.values()]
}
