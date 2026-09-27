// What this site keeps on your device, and how to get rid of it.
//
// FicAtlas is deliberately account-optional: reading progress, bookmarks,
// recent searches, reader preferences and the mute list all live in
// localStorage, and saved works live in IndexedDB. That is good for privacy —
// none of it is on a server — but it left a gap. Nineteen keys accumulated with
// no page that admitted they existed, no way to clear any one of them, and no
// way to take them with you.
//
// "It never leaves your device" is only half a privacy promise. The other half
// is being able to see it and delete it.

import { keysForGroup } from "./storageKeys"

export interface DataGroup {
  id: string
  name: string
  hint: string
  keys: string[]
}

// Grouped by what a person would want to clear, not by how it is stored. Reader
// preferences and reading progress are both "reader" data internally and are
// completely different things to lose: one is a setting, the other is your place
// in forty stories.
//
// The KEYS come from lib/storageKeys.ts and are not listed here. They used to
// be, and the list had drifted from the one the Settings page writes: "Clear
// preferences" left `default_sites`, `default_sort`, `results_per_page`,
// `show_explicit`, `show_underage` and `shelf_sort` standing, so a reader who
// pressed it kept six of their fourteen settings — including both content
// toggles — with nothing to say it had only half worked. It also cleared
// `ficatlas:settings`, which nothing has ever written.
export const DATA_GROUPS: DataGroup[] = [
  {
    id: "history",
    name: "Recent searches",
    hint: "The list that appears under the search box, and any searches you kept.",
    keys: keysForGroup("history"),
  },
  {
    id: "progress",
    name: "Reading progress",
    hint: "Where you had got to in each story, and remembered scroll positions.",
    keys: keysForGroup("progress"),
  },
  {
    id: "bookmarks",
    name: "Bookmarks",
    hint: "Works you saved to your shelf, and the ones kept for offline reading.",
    keys: keysForGroup("bookmarks"),
  },
  {
    id: "mutes",
    name: "Never-show-me list",
    hint: "Everything you have chosen to hide from search.",
    keys: keysForGroup("mutes"),
  },
  {
    id: "prefs",
    name: "Preferences",
    hint: "Theme, reader font, text size, line width, default archives, sort, "
        + "results per page and both content toggles.",
    keys: keysForGroup("prefs"),
  },
]

/** Roughly how much space a group occupies. Enough to tell "nothing" from
 *  "something"; not worth being exact about for a few kilobytes of JSON. */
export function groupSize(group: DataGroup): number {
  if (typeof window === "undefined") return 0
  let total = 0
  for (const k of group.keys) {
    const v = localStorage.getItem(k)
    // UTF-16 in practice, so two bytes a character is the honest estimate.
    if (v) total += (k.length + v.length) * 2
  }
  return total
}

export function clearGroup(group: DataGroup): void {
  for (const k of group.keys) {
    try { localStorage.removeItem(k) } catch { /* private mode */ }
  }
}

/** Everything, as one JSON file.
 *
 *  Not a backup format anything reads back yet — it is the answer to "what do
 *  you have on me?", which should never require trusting our summary of it. */
export function exportAll(): string {
  const out: Record<string, unknown> = {
    exported_at: new Date().toISOString(),
    // The note travels inside the downloaded file, so it has to be true on its
    // own without the page around it. It said "on your device only", which is
    // right for a signed-out reader and wrong for a signed-in one — most of
    // this is mirrored to the account. It cannot ask who is reading it, so it
    // says both.
    note: "Everything FicAtlas keeps about you in this browser. With an account "
        + "most of it is also mirrored to the account so it follows you between "
        + "devices; signed out, this browser is the only copy. Saved offline "
        + "works are stored separately in IndexedDB and are not included here.",
  }
  const data: Record<string, unknown> = {}
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i)
    if (!k || !k.startsWith("ficatlas:")) continue
    const raw = localStorage.getItem(k)
    if (raw == null) continue
    // Parse where we can so the file is readable rather than a wall of escaped
    // strings; fall back to the raw value for anything that is not JSON.
    try { data[k] = JSON.parse(raw) } catch { data[k] = raw }
  }
  out.data = data
  return JSON.stringify(out, null, 2)
}

export function downloadExport(): void {
  const blob = new Blob([exportAll()], { type: "application/json" })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = `ficatlas-data-${new Date().toISOString().slice(0, 10)}.json`
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revoked on the next tick: revoking immediately can cancel the download in
  // some browsers before it has started reading the blob.
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
