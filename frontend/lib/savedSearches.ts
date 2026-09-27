// Searches the reader chose to keep.
//
// Why this is not "recent searches"
// ---------------------------------
// Recents are a LOG: written automatically on every run, capped at 50, and
// overwritten without asking. That is right for "what was I just doing" and
// useless for the thing readers of a fic index actually do — hold a standing
// question. "Complete drarry over 100k that I have not read" is not a thing you
// type once; it is a thing you come back to for months, and retyping it is how
// you end up with a slightly different query and a different answer.
//
// A saved search is one STRING, and that is deliberate
// ----------------------------------------------------
// The search bar's own syntax already carries filters, status, word count and
// sort (`fandom:"Harry Potter" tag:"Time Travel" complete words:>100k`), and
// api/search.py re-parses `q` on the way in. So the whole request fits in the
// one field the reader can see, edit and paste — the same argument
// /api/search/extract follows in putting status and word count INTO the query
// rather than beside it. A structured record would need a migration every time
// a filter is added, and would not survive being pasted to a friend.
//
// Why there is no new-results notification queue
// -------------------------------------------------
// Same reasoning as api/follows.py, and worth restating because the temptation
// is the same: an update is a COMPARISON, not an event. Each entry remembers how
// many works it matched when it was last run, so re-running it can say "+4 since
// you last looked" for the cost of a number already in the response. What it
// deliberately does NOT do is re-run everybody's saved searches on a timer to
// find out — a search here materialises up to 5,001 candidates and ranks every
// one, so a background sweep over saved searches would be the most expensive job
// on the box in exchange for telling readers something they find out anyway the
// next time they look.
//
// The cost is that the count is as of your last visit rather than as of now,
// which is what a reader means by "new" anyway.

export interface SavedSearch {
  /** Stable, and the key the server's merge dedups on — see _merge_id_array in
   *  api/userdata.py. Derived from the query so that saving the same search on
   *  two devices produces one entry rather than two. */
  id: string
  /** The search bar string. The whole request. */
  q: string
  /** When it was saved, ISO. Ordering is most-recently-saved first. */
  at: string
  /** How many works it matched when last run, and when that was. Absent until
   *  it has been run once since being saved. */
  last_total?: number
  last_run?: string
}

const KEY = "ficatlas:saved-searches"

/** Fired when the list changes, so an open search page updates without a
 *  reload. Matches the MUTES_CHANGED pattern in lib/mutelist.ts. */
export const SAVED_CHANGED = "ficatlas:saved-changed"

// Enough for a standing set of questions, small enough that the sync payload
// stays trivial. A reader with 30 saved searches has a different problem.
export const MAX_SAVED = 30

/** Two queries are the same search if they differ only in spacing or case.
 *  Without this, saving from the bar and saving the same thing after a filter
 *  click produces two entries that both have to be deleted later. */
export function searchId(q: string): string {
  return q.trim().toLowerCase().replace(/\s+/g, " ")
}

export function loadSaved(): SavedSearch[] {
  if (typeof window === "undefined") return []
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    // Field by field, like loadMutes: a partial or older object must not produce
    // an entry whose `q` is undefined and which then renders as a blank row that
    // cannot be clicked or removed.
    return parsed
      .filter(x => x && typeof x.q === "string" && x.q.trim())
      .map(x => ({
        id: typeof x.id === "string" && x.id ? x.id : searchId(x.q),
        q: x.q,
        at: typeof x.at === "string" ? x.at : new Date(0).toISOString(),
        last_total: typeof x.last_total === "number" ? x.last_total : undefined,
        last_run: typeof x.last_run === "string" ? x.last_run : undefined,
      }))
      .sort((a, b) => b.at.localeCompare(a.at))
  } catch {
    // Corrupt JSON must not break the search page. An empty list is the safe
    // failure: the reader loses a menu, not the ability to search.
    return []
  }
}

function write(list: SavedSearch[]): void {
  if (typeof window === "undefined") return
  try {
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX_SAVED)))
    window.dispatchEvent(new CustomEvent(SAVED_CHANGED))
  } catch {
    // Quota or private mode. Nothing to do, and nothing worth interrupting a
    // search for.
  }
}

export function isSaved(q: string): boolean {
  const id = searchId(q)
  return loadSaved().some(s => s.id === id)
}

/** Save, or move an existing entry to the top. Returns the new list. */
export function saveSearch(q: string, total?: number): SavedSearch[] {
  const trimmed = q.trim()
  if (!trimmed) return loadSaved()
  const id = searchId(trimmed)
  const now = new Date().toISOString()
  const existing = loadSaved().find(s => s.id === id)
  const entry: SavedSearch = {
    id,
    // Keep the reader's own spacing and capitals; `id` is what dedups.
    q: trimmed,
    at: now,
    // Saving a search you are looking at records what it matched, so the first
    // re-run has something to compare against rather than starting blind.
    last_total: total ?? existing?.last_total,
    last_run: total != null ? now : existing?.last_run,
  }
  const next = [entry, ...loadSaved().filter(s => s.id !== id)]
  write(next)
  return next
}

export function removeSaved(id: string): SavedSearch[] {
  const next = loadSaved().filter(s => s.id !== id)
  write(next)
  return next
}

/** Record what a saved search matched, if this query is one. Called after a
 *  search runs; a no-op for the overwhelming majority of searches, which are
 *  not saved. */
export function noteRun(q: string, total: number): void {
  const id = searchId(q)
  const list = loadSaved()
  const hit = list.find(s => s.id === id)
  if (!hit) return
  if (hit.last_total === total && hit.last_run) return   // nothing to write
  write(list.map(s => s.id === id
    ? { ...s, last_total: total, last_run: new Date().toISOString() }
    : s))
}

/** How many more works this search matches than when it was last run, or null
 *  if it has never been run or has not grown.
 *
 *  Clamped at zero and null-on-equal for the same reason api/follows.py clamps
 *  new_chapters: a result set can SHRINK — a work delisted, an author opting
 *  out, a content-gate list getting stricter — and "-3 new" is not a thing to
 *  show anybody. */
export function newSince(s: SavedSearch, total: number): number | null {
  if (s.last_total == null) return null
  const gain = total - s.last_total
  return gain > 0 ? gain : null
}
