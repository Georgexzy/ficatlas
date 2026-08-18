// Per-reader preferences, kept on the device.
//
// Settings used to be one flat instance-wide table, which was right when the
// only user was the person running the box. On a public site it is wrong twice
// over: one reader's choice of serif would change everyone's, and the whole page
// was gated behind an admin account, so a signed-out reader could see a font
// picker and a "Save settings" button that did nothing.
//
// So preferences split by WHO THEY BELONG TO rather than by topic:
//
//   here                what this reader wants — theme, font, width, the sites
//                       and sort their search starts from. No account needed,
//                       because none of it leaves the device.
//   /api/settings       what the INSTANCE does — what it crawls, how often, what
//                       the feed keeps. Admin only, and genuinely shared.
//
// The server value stays useful as the starting point: an operator who indexes
// only FictionAlley can set that as the default every first-time visitor gets, and
// any reader can then choose otherwise. Read order is device → server → built-in,
// which is the order the reader page already used for font and width.
export interface Prefs {
  default_sites: string
  default_sort: string
  results_per_page: string
  show_explicit: string
  reader_font: string
  reader_width: string
}

export const PREF_KEYS: (keyof Prefs)[] = [
  "default_sites", "default_sort", "results_per_page", "show_explicit",
  "reader_font", "reader_width",
]

// The reader page has written these two under exactly these names since before
// this module existed, and there are devices carrying them. Keeping the naming
// scheme means an existing reader's font choice survives the change rather than
// silently reverting to serif.
const key = (k: keyof Prefs) => `ficatlas:${k}`

export function readPref(k: keyof Prefs): string | null {
  if (typeof window === "undefined") return null
  try {
    return localStorage.getItem(key(k))
  } catch {
    // Safari in private mode throws on localStorage rather than returning null.
    // A reader with no persistence should still get a working site on defaults.
    return null
  }
}

export function writePref(k: keyof Prefs, value: string): void {
  if (typeof window === "undefined") return
  try {
    localStorage.setItem(key(k), value)
  } catch { /* see readPref */ }
}

export function readAllPrefs(): Partial<Prefs> {
  const out: Partial<Prefs> = {}
  for (const k of PREF_KEYS) {
    const v = readPref(k)
    if (v !== null) out[k] = v
  }
  return out
}

/** Instance defaults overlaid with this reader's own choices. */
export function mergePrefs(serverDefaults: Partial<Prefs>): Partial<Prefs> {
  return { ...serverDefaults, ...readAllPrefs() }
}
