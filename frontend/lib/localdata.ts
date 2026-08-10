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
export const DATA_GROUPS: DataGroup[] = [
  {
    id: "history",
    name: "Recent searches",
    hint: "The list that appears under the search box.",
    keys: ["ficatlas:recent-searches", "ficatlas:recents", "ficatlas:last-search"],
  },
  {
    id: "progress",
    name: "Reading progress",
    hint: "Where you had got to in each story, and remembered scroll positions.",
    keys: ["ficatlas:progress", "ficatlas:scroll-memory"],
  },
  {
    id: "bookmarks",
    name: "Bookmarks",
    hint: "Works you saved to your shelf.",
    keys: ["ficatlas:bookmarks", "ficatlas:shelf_sort"],
  },
  {
    id: "mutes",
    name: "Never-show-me list",
    hint: "Everything you have chosen to hide from search.",
    keys: ["ficatlas:mutes"],
  },
  {
    id: "prefs",
    name: "Preferences",
    hint: "Theme, reader font, text size, line width, default archives.",
    keys: ["ficatlas:settings", "ficatlas:theme", "ficatlas:reader_font",
           "ficatlas:reader-fontsize", "ficatlas:reader_justify",
           "ficatlas:reader_lineheight", "ficatlas:reader_theme",
           "ficatlas:reader_width", "ficatlas:sidebar_w"],
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
    note: "FicAtlas keeps this on your device only. Saved offline works are "
        + "stored separately in IndexedDB and are not included here.",
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
