// Every `ficatlas:` key the app stores, and which of them follow the reader.
//
// Why this file exists
// -------------------
// There were THREE lists of what a preference is, written independently, and
// they had drifted. Measured across the 14 preference keys the app actually
// writes, only TWO — reader_font and reader_width — appeared in all three:
//
//   lib/prefs.ts PREF_KEYS      what the Settings page offers          7 keys
//   lib/auth.tsx PREF_KEYS      what sync sends to the account         8 keys
//   lib/localdata.ts prefs      what "Clear preferences" removes       8 keys
//
// Each disagreement was a feature quietly not working:
//
//   * `default_sort`, `results_per_page`, `show_explicit` and `show_underage`
//     were offered in Settings and never synced. Both content toggles are in
//     that list, so a reader who turned adult content on at their laptop got
//     the default back on their phone, with nothing to say why.
//   * Six keys survived "Clear preferences", including the two content toggles
//     and the default archives — a reset button that left half the settings
//     standing.
//   * `ficatlas:settings` was cleared and has never been written by anything.
//
// The same shape as the gate-term drift recorded in CLAUDE.md, and the same
// fix: one list, imported rather than copied, with a test that fails if a
// consumer grows its own.
//
// Adding a key
// ------------
// Put it here or it is invisible to sync, to the data page and to the export.
// `scope` is a decision about whose the value is, and the `why` is required
// because "should this follow me to my phone?" has a real answer per key and
// the answer is not guessable from the name — reader_theme follows a reader and
// scroll-memory cannot.

export type Scope =
  /** Mirrored to the account and adopted on every device. */
  | "sync"
  /** Stays on this device, deliberately. `why` says what makes it local. */
  | "device"

export interface StoredKey {
  /** Without the `ficatlas:` prefix. */
  key: string
  scope: Scope
  /** Which group on the data page owns it, for display and for clearing. */
  group: "prefs" | "history" | "progress" | "bookmarks" | "mutes" | "internal"
  why: string
}

export const STORED_KEYS: StoredKey[] = [
  // ---- Preferences the reader sets deliberately -------------------------
  { key: "theme", scope: "sync", group: "prefs",
    why: "Light or dark. reader_theme already followed the reader and this did "
       + "not, so the site and the reader page could disagree on one device." },
  { key: "reader_font", scope: "sync", group: "prefs",
    why: "A reading choice, and the same eyes on every device." },
  { key: "reader_width", scope: "sync", group: "prefs", why: "As reader_font." },
  { key: "reader_theme", scope: "sync", group: "prefs", why: "As reader_font." },
  { key: "reader_lineheight", scope: "sync", group: "prefs", why: "As reader_font." },
  { key: "reader-fontsize", scope: "sync", group: "prefs",
    why: "As reader_font. The hyphen is historical — devices carry it, so "
       + "renaming it would silently reset text size for existing readers." },
  { key: "reader_justify", scope: "sync", group: "prefs", why: "As reader_font." },
  { key: "default_sites", scope: "sync", group: "prefs",
    why: "Which archives a search starts from." },
  { key: "default_sort", scope: "sync", group: "prefs",
    why: "Offered in Settings and never synced until now." },
  { key: "results_per_page", scope: "sync", group: "prefs",
    why: "Offered in Settings and never synced until now." },
  { key: "show_explicit", scope: "sync", group: "prefs",
    why: "A deliberate content choice, made once in Settings. It failed to "
       + "travel, so the reader had to find the toggle again on every device. "
       + "Staleness is in the safe direction either way: the default is off, "
       + "so a sync that has not happened hides rather than reveals." },
  { key: "show_underage", scope: "sync", group: "prefs",
    why: "As show_explicit, and for the same safe-default reason." },
  { key: "shelf_sort", scope: "sync", group: "prefs",
    why: "How the shelf is ordered. The shelf itself syncs; its ordering not "
       + "syncing meant the same books in a different order on each device." },
  { key: "sidebar_w", scope: "sync", group: "prefs",
    why: "Filter sidebar width. Borderline — it is a pixel count, and a width "
       + "chosen on a wide monitor is not a width for a laptop. Kept synced "
       + "because it already was, it is clamped on read, and the sidebar is "
       + "not rendered at phone widths at all." },

  // ---- The reader's own content ----------------------------------------
  { key: "bookmarks", scope: "sync", group: "bookmarks",
    why: "The shelf. The single most-cited reason to make an account." },
  { key: "progress", scope: "sync", group: "progress",
    why: "Where you had got to. Merged per story and per chapter server-side, "
       + "so two devices reading the same work never lose the other's place." },
  { key: "recent-searches", scope: "sync", group: "history",
    why: "The list under the search box. Worth carrying because a fic search is "
       + "often abandoned and resumed somewhere else — you try something on a "
       + "phone, give up, and want that query in front of you at a keyboard. A "
       + "log, though, not a keepsake: capped at 50 and overwritten without "
       + "asking, which is what saved-searches exists to be the opposite of." },
  { key: "offline-shelf", scope: "sync", group: "bookmarks",
    why: "WHICH works were chosen for offline reading, never the chapters — "
       + "those are megabytes and belong to the device that downloaded them." },
  { key: "saved-searches", scope: "sync", group: "history",
    why: "Searches the reader deliberately kept, as opposed to recent-searches "
       + "which is a log. A named query is the one thing a cross-archive index "
       + "has that an archive's own saved search cannot express." },

  { key: "mutes", scope: "sync", group: "mutes",
    why: "The never-show-me list. It was deliberately device-only, on the "
       + "argument that a standing list of ships, tropes and authors somebody "
       + "refuses to read is more revealing than any search. Changed on the "
       + "operator's instruction, for consistency: it is a curated list the "
       + "reader built by hand, it sits in Settings beside a dozen preferences "
       + "that all follow them, and losing it on a new phone is losing work. "
       + "The privacy argument was not wrong, so it is answered rather than "
       + "dropped — /privacy names this list explicitly among what is stored, "
       + "and the Settings copy no longer promises it stays put." },

  // ---- Deliberately local ----------------------------------------------
  { key: "scroll-memory", scope: "device", group: "progress",
    why: "Pixel offsets in a rendered page. A position measured on a phone "
       + "means nothing on a laptop, and restoring it would land the reader in "
       + "the wrong place with no way to tell why." },
  { key: "navstack", scope: "device", group: "internal",
    why: "Where Back goes inside this tab. Has no meaning outside it." },
  { key: "last-search", scope: "device", group: "history",
    why: "The query to restore when this tab comes back to the search page. "
       + "Per-tab continuity, not a record worth keeping." },
  { key: "tips-dismissed", scope: "sync", group: "internal",
    why: "Whether this reader has dismissed the one-off note under the results "
       + "explaining what an account adds. Per-device it would reappear on "
       + "every device they use, which is the behaviour EmailPrompt's comment "
       + "already calls out as the site not listening." },
  { key: "email-prompt-dismissed", scope: "sync", group: "internal",
    why: "Whether this reader has said no to adding an email. Device-scoped, "
       + "it asked again on every device — which reads as the site not "
       + "listening. Saying no once is an answer about the account." },
  { key: "me", scope: "device", group: "internal",
    why: "Cache of the last identity the server confirmed, so being offline is "
       + "not mistaken for being signed out. Never sent anywhere: it is a copy "
       + "OF the server's answer, and syncing it would be circular." },
  { key: "recents", scope: "device", group: "history",
    why: "Legacy. Nothing has written it since searches moved to "
       + "recent-searches; listed so the data page can still clear it off a "
       + "device that carries one." },
  { key: "settings", scope: "device", group: "prefs",
    why: "Legacy, and never written by anything — the sync layer synthesises a "
       + "`settings` object from the individual pref keys instead. Kept only "
       + "so a device carrying a stale copy can be cleaned." },
]

const byScope = (s: Scope) => STORED_KEYS.filter(k => k.scope === s).map(k => k.key)

/** Preference keys that ride inside the synced `settings` object.
 *  This is the list lib/auth.tsx sends; it must not be re-typed there. */
export const SYNCED_PREF_KEYS: string[] =
  STORED_KEYS.filter(k => k.scope === "sync" && k.group === "prefs").map(k => k.key)

/** Keys that stay on the device, whatever the reader's account does. */
export const DEVICE_ONLY_KEYS: string[] = byScope("device")

/** Every key, prefixed, for the data page and the export. */
export const ALL_STORAGE_KEYS: string[] = STORED_KEYS.map(k => `ficatlas:${k.key}`)

/** The keys in one data-page group, prefixed. */
export function keysForGroup(group: StoredKey["group"]): string[] {
  return STORED_KEYS.filter(k => k.group === group).map(k => `ficatlas:${k.key}`)
}
