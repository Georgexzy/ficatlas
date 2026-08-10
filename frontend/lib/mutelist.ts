// A reader's persistent "never show me" list.
//
// Search has always supported exclusions, but only for the search you are
// running: type them, get results, and they are gone the next time. The thing
// people actually want from a fanfic index is standing — a ship they will never
// read, a trope they bounce off, an author they would rather not see — applied
// to every search without being retyped.
//
// Per device, in localStorage, matching every other reader setting here: it
// works signed-out, it needs no account, and a list of ships someone refuses to
// read never leaves their machine. That last part is the reason to prefer this
// even though it does not follow you to your phone — it is a genuinely
// revealing list, and the safest place to keep it is nowhere we can see.
//
// Stored as one object under one key so a later "sync to account" can lift it
// whole without a migration.

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

/** Fired when the list changes, so an open search page can re-run. */
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
