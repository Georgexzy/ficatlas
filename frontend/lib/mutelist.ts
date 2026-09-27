// A reader's persistent "never show me" list.
//
// Search has always supported exclusions, but only for the search you are
// running: type them, get results, and they are gone the next time. The thing
// people actually want from a fanfic index is standing — a ship they will never
// read, a trope they bounce off, an author they would rather not see — applied
// to every search without being retyped.
//
// In localStorage, so it works signed-out and needs no account. With an account
// it SYNCS, like everything else in Settings — see lib/storageKeys.ts.
//
// It did not, originally, and the argument for that is worth keeping on the
// record because it was a real one: a standing list of ships, tropes and authors
// somebody refuses to read is more revealing than any single search, and the
// safest place for it was nowhere we could see. It syncs now by decision, for
// consistency and because rebuilding a curated list on a new phone is losing
// work. What the old argument bought instead is that the list is now named
// explicitly on /privacy rather than lumped in with "your settings", and it
// still never enters a shared search link.
//
// One object under one key, which is what made that change a one-line edit
// rather than a migration.

export interface MuteList {
  tags: string[]
  relationships: string[]
  fandoms: string[]
  characters: string[]
  authors: string[]
}

export const EMPTY_MUTES: MuteList = {
  tags: [], relationships: [], fandoms: [], characters: [], authors: [],
}

const KEY = "ficatlas:mutes"

/** Fired when the list changes.
 *
 *  Nothing listens for it today, and that is not a gap: lib/api.ts calls
 *  loadMutes() on every search, so the next search always uses the current list.
 *  The event exists for a mute control placed ON the search page, where the
 *  results already on screen would need re-running — it is an extension point,
 *  not a wire that came loose. */
export const MUTES_CHANGED = "ficatlas:mutes-changed"

export function loadMutes(): MuteList {
  if (typeof window === "undefined") return EMPTY_MUTES
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return EMPTY_MUTES
    const parsed = JSON.parse(raw)
    // Field by field: a partial or older object must not produce undefined
    // arrays that then throw on .length at the call site.
    return {
      tags: arr(parsed.tags),
      relationships: arr(parsed.relationships),
      fandoms: arr(parsed.fandoms),
      characters: arr(parsed.characters),
      authors: arr(parsed.authors),
    }
  } catch {
    // Corrupt JSON must not break search. An empty list is the safe failure:
    // it shows too much rather than silently hiding things.
    return EMPTY_MUTES
  }
}

function arr(v: unknown): string[] {
  return Array.isArray(v) ? v.filter(x => typeof x === "string" && x.trim()) : []
}

export function saveMutes(mutes: MuteList): void {
  if (typeof window === "undefined") return
  try {
    localStorage.setItem(KEY, JSON.stringify(mutes))
    window.dispatchEvent(new CustomEvent(MUTES_CHANGED))
  } catch {
    // Quota or a private-mode restriction. Nothing to do and nothing worth
    // interrupting the reader for.
  }
}

export function muteCount(m: MuteList): number {
  return m.tags.length + m.relationships.length + m.fandoms.length
       + m.characters.length + m.authors.length
}

/** Merge the standing list into one search's exclusions, without duplicates. */
export function withMutes(
  params: URLSearchParams, mutes: MuteList,
): URLSearchParams {
  const merge = (key: string, values: string[]) => {
    if (!values.length) return
    const existing = (params.get(key) ?? "").split(",").map(s => s.trim()).filter(Boolean)
    // Case-insensitive dedupe: the same tag typed into a search and into the
    // mute list should not be sent twice in different capitalisations.
    const seen = new Set(existing.map(v => v.toLowerCase()))
    const merged = [...existing]
    for (const v of values) {
      if (!seen.has(v.toLowerCase())) { merged.push(v); seen.add(v.toLowerCase()) }
    }
    params.set(key, merged.join(","))
  }
  merge("exclude_tags", mutes.tags)
  merge("exclude_relationships", mutes.relationships)
  merge("exclude_fandoms", mutes.fandoms)
  merge("exclude_characters", mutes.characters)
  merge("exclude_authors", mutes.authors)
  return params
}
