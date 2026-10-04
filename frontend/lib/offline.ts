// Offline story storage backed by IndexedDB.
//
// Why IndexedDB and not localStorage: full chapter text for a long fic can be
// several megabytes — well past localStorage's ~5MB total budget. IndexedDB has
// no practical cap for our purposes and stores structured data directly.
//
// Layout: one object store "stories" keyed by story id. Each record holds the
// story metadata plus an array of {number, title, content} chapters, so a saved
// story is fully self-contained and readable with no network.

import {
  fetchWithTimeout,
  BACKGROUND_TIMEOUT_MS,
  USER_TIMEOUT_MS,
  DOWNLOAD_TIMEOUT_MS,
} from "./net"

export interface OfflineChapter {
  number: number
  title?: string
  content: string
  start_note?: string
  end_note?: string
  summary?: string
}

export interface OfflineStory {
  /** Chapters not yet fetched because the download was cut short or the server
   *  was unwell. Retryable, and the ONLY field `resumeInterruptedDownloads`
   *  acts on — so a chapter recorded here is one the reader can still get. */
  missingChapters?: number[]
  /** Chapters the server answered 404 for: the archive does not have them and
   *  never will, because a chapter number that once resolved and now does not
   *  is a deletion rather than an outage.
   *
   *  Kept apart from `missingChapters` because the two want opposite treatment.
   *  Folding them together — which is what recording a 404 as missing does —
   *  makes `resumeInterruptedDownloads` re-download the entire work on every
   *  library open and every `online` event, forever, in pursuit of a chapter
   *  that is not there. For a 199-chapter novel that is 199 requests per page
   *  load, and it is the kind of thing that gets a reader's IP throttled.
   *
   *  Shown to the reader as part of the hole count, because from where they sit
   *  "chapter 90 is not on this device" and "chapter 90 does not exist" are the
   *  same fact: they cannot read it. Absent on every record written before this
   *  field existed, which is correct — those downloads skipped 404s silently
   *  rather than recording them. */
  absentChapters?: number[]
  /** Chapters that had text when saved. Absent on records written before this
   *  existed, which is why the audit falls back to "any text at all". */
  textChapters?: number
  id: string
  title: string
  author: string
  site: string
  url: string
  summary?: string
  fandoms?: string[]
  word_count?: number
  /** How many chapters THIS DEVICE HOLDS. Not the work's length — a partial save
   *  of a 199-chapter work stores 4 here. See `chapter_count_total`. */
  chapter_count: number
  chapters: OfflineChapter[]
  savedAt: string   // ISO timestamp
  /** Approximate bytes this record occupies. Absent on records written by v1. */
  bytes?: number
  /** Schema version that wrote this record, so migrations can be selective. */
  schema?: number

  // ── Everything below arrived in v3 ──────────────────────────────────────
  //
  // The story page is where a reader LANDS after reading a saved chapter, and it
  // renders a metadata block: rating, kudos, hits, updated date, completion
  // status, fandoms, relationships, characters, tags. Until v3 the save kept
  // none of that, and the page filled the gaps with `status: "unknown"`,
  // `kudos: 0`, `language: "English"` and empty tag arrays.
  //
  // Those are not blanks, they are assertions, and three of them were wrong in a
  // way that changed a reader's decision. A work with 1,039 kudos and 59,744
  // hits rendered with no kudos row at all, because the render hides anything
  // under `kudos > 0` — so a popular work looked unread. A work marked Complete
  // rendered "Not stated", which is the same claim STATUS_LABEL goes out of its
  // way to avoid for the 5.3M FF.net works whose completion is genuinely unknown.
  // And the cast, the pairings and the tags — the three things a reader browses a
  // story page by — were simply gone.
  //
  // All of it is already in hand when the save runs: it fetches
  // `/api/stories/{id}` for the title and word count before it fetches a single
  // chapter. Storing it costs a few hundred bytes against a record that holds
  // megabytes of chapter text, and it is the difference between a saved page that
  // is honest and one that is confidently wrong.
  //
  // All optional, because records written by v1 and v2 are still on readers'
  // devices and a missing field must degrade to "not shown" rather than to a
  // fabricated value.

  /** The work's real chapter count, so the page can read "4/199" on a partial
   *  save. `chapter_count` alone rendered "4/?", which told a reader nothing
   *  about whether they had the whole work. */
  chapter_count_total?: number
  status?: string
  rating?: string
  kudos?: number
  hits?: number
  bookmarks?: number
  comments?: number
  language?: string
  published_at?: string
  updated_at?: string
  relationships?: string[]
  characters?: string[]
  tags?: string[]
  warnings?: string[]
  categories?: string[]
}

/** Bumped whenever the record shape changes; written into each record. */
export const SCHEMA_VERSION = 3

const DB_NAME = "ficatlas-offline"
// Version 2 exists to establish that migrating is possible at all, before it is
// needed. v1 shipped with an onupgradeneeded that only created the store if it
// was missing, so there was no path to changing the record shape later — and the
// only way out of that is to discard what readers already have, which is the
// exact failure this whole module exists to prevent. The upgrade below is a
// no-op for existing records by design; it is the hinge, not the change.
const DB_VERSION = 2
const STORE = "stories"

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("IndexedDB not available"))
      return
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = (event) => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "id" })
        return
      }
      // Migrations run in order from whatever version this device holds. Each
      // step must be safe to apply to a record written by any earlier version,
      // because a device can be arbitrarily far behind.
      const from = event.oldVersion
      if (from < 2) {
        // v1 -> v2: stamp existing records with the schema version that wrote
        // them, so a future migration can tell them apart without guessing.
        const store = req.transaction!.objectStore(STORE)
        store.openCursor().onsuccess = (e) => {
          const cur = (e.target as IDBRequest<IDBCursorWithValue>).result
          if (!cur) return
          const rec = cur.value
          if (rec && rec.schema == null) { rec.schema = 1; cur.update(rec) }
          cur.continue()
        }
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
    // Another tab holding an old-version connection blocks the upgrade forever
    // and the save silently hangs. Fail loudly instead.
    req.onblocked = () =>
      reject(new Error("Another FicAtlas tab is open with an older version. "
                     + "Close it and try again."))
  })
}

// ── Durability ──────────────────────────────────────────────────────────────
//
// Browsers evict origin storage under disk pressure, least-recently-used origin
// first, and iOS additionally clears storage for a PWA that has not been added
// to the home screen after seven days. So "saved for offline" was, by default, a
// promise the browser was free to break without telling anybody — which is the
// single most-reported failure of comparable reader apps: people lose whole
// downloaded libraries and only find out when they are offline and it matters.
//
// navigator.storage.persist() asks for exemption from eviction.
//
// The comment here used to say "Safari does not implement it", and that belief
// shaped the whole design — if the browser most likely to evict could not be
// asked, there was no point asking early. It is out of date. WebKit's current
// storage policy implements persist() and grants it on heuristics, listing
// "whether the website is opened as a Home Screen Web App" as one of them. The
// same policy gives an installed web app the same quota as a browser — up to
// 60% of total disk per origin — not the 50MB figure that circulates.
//
// So on iOS the answer is very likely YES, and only if we ask. Without
// persistence a site is in best-effort mode with no guarantees: WebKit evicts
// least-recently-used data under storage pressure, over quota, or after
// prolonged inactivity.
//
// Chrome grants it for installed PWAs, bookmarked sites or push permission;
// Firefox prompts. It cannot be forced anywhere, so the honest design is to ask
// at the moment most likely to succeed, and tell the reader what the answer was.

export type PersistState = "persisted" | "denied" | "unsupported"

export async function requestPersistentStorage(): Promise<PersistState> {
  try {
    if (typeof navigator === "undefined" || !navigator.storage?.persist) return "unsupported"
    if (await navigator.storage.persisted?.()) return "persisted"
    // Asked again on every save rather than only the first.
    //
    // Chrome decides this on engagement — an installed PWA, a bookmark, push
    // permission, repeat visits — and none of those are true of someone on
    // their first visit, which is exactly when the old code asked its one and
    // only time. A reader who comes back and saves a third story has earned
    // signals they did not have at the first, and the call is free.
    return (await navigator.storage.persist()) ? "persisted" : "denied"
  } catch {
    return "unsupported"
  }
}

/** Current persistence state without asking for it. */
export async function persistenceState(): Promise<PersistState> {
  try {
    if (typeof navigator === "undefined" || !navigator.storage?.persisted) return "unsupported"
    return (await navigator.storage.persisted()) ? "persisted" : "denied"
  } catch {
    return "unsupported"
  }
}

/** Check every saved story is still there and still complete.
 *
 *  Downloads do not fail loudly; they disappear. Browsers evict IndexedDB under
 *  storage pressure whenever persistence has not been granted, and Safari does
 *  it on a timer — its tracking prevention clears storage for sites the reader
 *  has not returned to in seven days, which is precisely the pattern of someone
 *  who saves a long fic for a flight. Persistence is refused far more often than
 *  granted: it is unimplemented in Safari and Chrome only grants it on
 *  engagement signals like installing the site.
 *
 *  So the saved list cannot be trusted as a record of what is actually readable.
 *  This reads each entry back and reports the ones that are gone or truncated —
 *  the difference between finding out at the Library, online, and finding out on
 *  a train.
 */
export interface OfflineAudit {
  ok: string[]
  broken: { id: string; title: string; reason: string }[]
}

export async function auditOfflineStories(): Promise<OfflineAudit> {
  const out: OfflineAudit = { ok: [], broken: [] }
  let saved: OfflineStory[]
  try {
    saved = await listOfflineStories()
  } catch {
    return out
  }
  for (const story of saved) {
    const chapters = story.chapters ?? []
    if (!chapters.length) {
      out.broken.push({ id: story.id, title: story.title,
                        reason: "no chapters saved" })
      continue
    }
    // Compared against what the save RECORDED, not against perfection. A story
    // can legitimately contain a chapter with no text; what indicates eviction
    // is text that has since gone missing.
    const withText = chapters.filter(c => c.content && c.content.trim()).length
    const expected = story.textChapters
    if (expected == null ? withText === 0 : withText < expected) {
      out.broken.push({
        id: story.id, title: story.title,
        reason: expected == null
          ? "no chapter text is left"
          : `${expected - withText} of ${expected} chapters have lost their text`,
      })
      continue
    }
    out.ok.push(story.id)
  }
  return out
}

/** Why offline reading cannot work here, or null if it can.
 *
 *  There is one failure that no amount of service-worker care can fix, and it is
 *  invisible from inside the app: service workers and the Cache API require a
 *  SECURE CONTEXT. https, or localhost — and nothing else. Reached over plain
 *  http at an IP address, which is how a self-hosted instance is usually opened
 *  from a phone on the same network or over a tailnet, `navigator.serviceWorker`
 *  does not merely fail, it does not exist.
 *
 *  Everything then behaves normally right up to the moment it matters. Pages
 *  load, stories save — IndexedDB is available on insecure origins — and reading
 *  works while the app stays open. Close it, lose the connection, tap the icon,
 *  and there is no cached shell to start from, so nothing appears at all.
 *
 *  Saying so is the only honest thing to do: the reader cannot deduce it, and
 *  the fix is not in the app.
 */
export type OfflineBlocker = { reason: "insecure-context"; detail: string } | null

export function offlineBlocker(): OfflineBlocker {
  if (typeof window === "undefined") return null
  if (window.isSecureContext && "serviceWorker" in navigator) return null
  return {
    reason: "insecure-context",
    detail: "This site is being served over http. Browsers only allow offline "
      + "caching on https (or localhost), so the app itself cannot be stored for "
      + "offline use. Saved stories are still on this device, but opening the app "
      + "with no connection will show nothing.",
  }
}

/** Byte sizes for people, not for machines. */
export function fmtBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 MB"
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`
  if (n >= 1024 ** 2) return `${Math.round(n / 1024 ** 2)} MB`
  return `${Math.max(1, Math.round(n / 1024))} KB`
}

export interface StorageEstimate { usage: number; quota: number; available: number }

export async function storageEstimate(): Promise<StorageEstimate | null> {
  try {
    if (typeof navigator === "undefined" || !navigator.storage?.estimate) return null
    const { usage = 0, quota = 0 } = await navigator.storage.estimate()
    return { usage, quota, available: Math.max(0, quota - usage) }
  } catch {
    return null
  }
}

/** The shelf: which works this reader chose to keep offline, and enough about
 *  them to show a list. Kept beside the stories rather than derived from them,
 *  because it has to survive on a device that never downloaded any of it —
 *  that is the entire point of syncing it.
 *
 *  Deliberately tiny: id, title, author. The chapters stay on the device that
 *  fetched them.
 */
export interface ShelfEntry { id: string; title: string; author?: string; savedAt: string }

const SHELF_KEY = "ficatlas:offline-shelf"

export function readShelf(): ShelfEntry[] {
  if (typeof window === "undefined") return []
  try {
    const raw = localStorage.getItem(SHELF_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.filter(e => e && typeof e.id === "string") : []
  } catch {
    return []
  }
}

function writeShelf(entries: ShelfEntry[]): void {
  try {
    localStorage.setItem(SHELF_KEY, JSON.stringify(entries))
    // The sync layer watches localStorage writes; this is what makes the shelf
    // travel. See lib/auth.tsx.
    window.dispatchEvent(new StorageEvent("storage", { key: SHELF_KEY }))
  } catch { /* private mode: the download still works, it just will not sync */ }
}

export function addToShelf(story: { id: string; title: string; author?: string }): void {
  const shelf = readShelf().filter(e => e.id !== story.id)
  shelf.unshift({ id: story.id, title: story.title, author: story.author,
                  savedAt: new Date().toISOString() })
  writeShelf(shelf)
}

export function removeFromShelf(id: string): void {
  writeShelf(readShelf().filter(e => e.id !== id))
}

/** Finish any download that a lost connection cut short.
 *
 *  Saving a long work over a phone connection is exactly the case where the
 *  connection goes away, and losing 150 fetched chapters because the train
 *  entered a tunnel is the wrong trade every time. An interrupted save keeps
 *  what it got and records the rest; this completes them.
 *
 *  Safe to call often — it is a no-op when nothing is partial — so it runs when
 *  the library opens and again the moment the browser reports coming back
 *  online, which is when it is most likely to succeed.
 */
export async function resumeInterruptedDownloads(
  onStory?: (title: string, done: number, total: number) => void,
): Promise<number> {
  if (typeof navigator !== "undefined" && !navigator.onLine) return 0
  // Only ONE resume at a time, per process.
  //
  // This is called on every `online` event as well as on load, and a browser
  // fires that event freely — flapping between wifi and mobile re-runs it
  // several times in a minute. Two runs at once means two sequential
  // re-downloads of the same stories, and two `saveStoryOffline` writes racing
  // on the same ids, which is a lost update at best and a corrupt record at
  // worst. There is no harm in refusing: the work is idempotent and the next
  // caller gets the same list.
  if (_resuming) return 0
  _resuming = true
  try {
    return await _resume(onStory)
  } finally {
    _resuming = false
  }
}

let _resuming = false

async function _resume(
  onStory?: (title: string, done: number, total: number) => void,
): Promise<number> {
  let saved: OfflineStory[]
  try {
    saved = await listOfflineStories()
  } catch {
    return 0
  }
  // `missingChapters` and NOT the hole count. A chapter the archive answered 404
  // for is recorded as absent precisely so that this filter does not match it:
  // otherwise every work with a deleted chapter in it is re-downloaded in full
  // on every page load, forever, chasing a chapter that is not there.
  const partial = saved.filter(s => (s.missingChapters?.length ?? 0) > 0)
  let finished = 0
  for (const story of partial) {
    try {
      await downloadStoryForOffline(story.id, (d, t) => onStory?.(story.title, d, t))
    } catch {
      // Still unreachable, or interrupted again. It stays partial and will be
      // picked up next time rather than being treated as a failure.
      continue
    }
    // Counted only once the record is re-read, because `downloadStoryForOffline`
    // RESOLVES on the lost-connection path too — it keeps what it fetched
    // rather than throwing. So incrementing straight after the await reported a
    // story that is still half-downloaded as finished, which is the one number
    // this function exists to produce and the one a reader watches while it
    // runs. A story whose chapters are all there now is finished; one that ran
    // out of connection again is not, and stays on the list for next time.
    const after = await getOfflineStory(story.id).catch(() => null)
    if ((after?.missingChapters?.length ?? 0) === 0) finished++
  }
  return finished
}

/** Chapters on this record that the reader cannot read.
 *
 *  Retryable holes and permanently absent ones together, because from where
 *  the reader sits they are the same fact: the chapter is not on this device.
 *  They are stored apart because they want opposite treatment from
 *  `resumeInterruptedDownloads`, not because the reader should be told apart
 *  from one another more than the copy manages.
 *
 *  One function so that "how much of this story is here" cannot have two
 *  answers: three call sites read this count and each had its own `?? 0` on a
 *  different field, which is how a story with a hole in the middle of it came
 *  to claim it was completely saved.
 */
export function offlineHoles(s: Pick<OfflineStory, "missingChapters" | "absentChapters"> | null | undefined): number {
  // A SET, not a sum: records saved before the download loop skipped known-404
  // chapters can hold one twice, and a chapter is one hole however many times
  // it was recorded.
  return new Set([...(s?.missingChapters ?? []), ...(s?.absentChapters ?? [])]).size
}

/** Whether anything here is worth fetching again. `absentChapters` never is. */
export function offlineRetryable(s: Pick<OfflineStory, "missingChapters"> | null | undefined): number {
  return s?.missingChapters?.length ?? 0
}

/** Put anything already downloaded onto the shelf.
 *
 *  The shelf is written when a work is saved, which does nothing for works saved
 *  BEFORE the shelf existed — and those are precisely the ones a reader has and
 *  expects to see on their other device. Without this, syncing appears simply
 *  not to work: the download is plainly there on the laptop and the phone shows
 *  an empty tab.
 *
 *  Runs whenever the library is opened. Idempotent, and it only ADDS: a work on
 *  the shelf that is not downloaded here is the normal cross-device state and
 *  must not be pruned, or the device without the file would delete the entry for
 *  the device that has it.
 */
export async function reconcileShelf(): Promise<number> {
  let saved: OfflineStory[]
  try {
    saved = await listOfflineStories()
  } catch {
    return 0
  }
  const shelf = readShelf()
  const known = new Set(shelf.map(e => e.id))
  const missing = saved.filter(s => !known.has(s.id))
  if (!missing.length) return 0
  writeShelf([
    ...missing.map(s => ({
      id: s.id, title: s.title, author: s.author,
      savedAt: s.savedAt || new Date().toISOString(),
    })),
    ...shelf,
  ])
  return missing.length
}

/** Is this shaped like a record the rest of this module can read?
 *
 *  A record is only as good as its weakest reader, and the weakest one is
 *  `savedStoryToDetail`, which does `.length` and `.map` on `chapters` during a
 *  React render. A hand-edited or truncated export file puts a record where
 *  `chapters` is not an array, and the consequence is not a bad story — it is a
 *  TypeError inside a render, so a BLANK story page with no error and nothing in
 *  the console pointing at the file the reader restored from. The same record
 *  also breaks `auditOfflineStories`, whose `.filter` throws far enough out that
 *  the whole audit fails and the Library reports nothing wrong with anything.
 *
 *  So both the writer and the importer ask this, and it asks about SHAPE rather
 *  than about plausibility. `saveStoryOffline` had no guard at all despite a
 *  comment in the importer claiming it applied "the same guard the writer
 *  applies" — one of the two was always doing nothing.
 */
function isUsableRecord(s: unknown): s is OfflineStory {
  if (!s || typeof s !== "object") return false
  const r = s as Record<string, unknown>
  if (typeof r.id !== "string" || !r.id) return false
  if (typeof r.title !== "string") return false
  // The array-ness of `chapters` is the one that actually crashes a render, so
  // it is required rather than defaulted. A record with no chapters is legal
  // (eviction can empty one) and is `[]`, not absent.
  if (!Array.isArray(r.chapters)) return false
  // The chapter numbers are read with `===` against a route parameter, so a
  // non-number here is a chapter that can never be found again.
  if (r.chapters.some(c => !c || typeof (c as { number?: unknown }).number !== "number")) return false
  return true
}

/** Exported for the test that holds this to what the comment above claims.
 *  The function itself is deliberately not part of the module's public surface —
 *  nothing outside should be deciding what a valid record is. */
export const isUsableRecordForTest = isUsableRecord

export async function saveStoryOffline(story: OfflineStory): Promise<void> {
  if (!isUsableRecord(story)) {
    throw new Error("That saved story is malformed and was not written.")
  }
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite")
    tx.objectStore(STORE).put(story)
    tx.oncomplete = () => { db.close(); resolve() }
    tx.onerror = () => {
      db.close()
      // A quota failure here is the one error a reader can actually act on, and
      // `tx.error` stringifies to nothing useful in the UI. Name it.
      const e = tx.error
      reject(e?.name === "QuotaExceededError"
        ? new Error("This device is out of space for saved stories. Remove one "
                  + "from your Library's Offline tab and try again.")
        : e)
    }
  })
}

/** Every saved work, for the data export. The shelf is the thing a reader is
 *  most likely to want to take with them, and `exportAll` could not reach it:
 *  it reads localStorage, and the works live in IndexedDB. So an export that
 *  omitted them was omitting the part people would miss.
 *
 *  Merged in memory rather than written into the JSON as base64: this is a file
 *  a person may open and read, and a wall of encoded chapter text would make it
 *  unreadable while being no more correct.
 *
 *  `warn` exists because returning `[]` for "there are none" and `[]` for "the
 *  store could not be read" are the same value, and only the second one is a
 *  problem. The caller cannot tell them apart, so a browser with IndexedDB
 *  disabled produced a file whose note promised "including the works you saved
 *  for offline reading" above an empty array — a reader would restore that file
 *  onto a new device, get nothing, and have no way to tell the export lied from
 *  the data being gone. The note is the only place in an export file a person
 *  will ever read, so the warning goes there. */
export async function exportOfflineStories(
  warn?: (message: string) => void,
): Promise<OfflineStory[]> {
  try {
    const db = await openDB()
    return await new Promise<OfflineStory[]>((resolve, reject) => {
      const out: OfflineStory[] = []
      const tx = db.transaction(STORE, "readonly")
      const req = tx.objectStore(STORE).openCursor()
      req.onsuccess = () => {
        const cur = req.result
        if (!cur) { db.close(); resolve(out); return }
        out.push(cur.value as OfflineStory)
        cur.continue()
      }
      req.onerror = () => { db.close(); reject(req.error) }
    })
  } catch {
    // A private-mode browser with IndexedDB disabled must still be able to
    // export whatever IS in localStorage. An empty shelf is the right answer
    // here, not a failed export — but it must SAY that it is empty for a reason
    // it could not check.
    warn?.("This browser would not let the page read saved offline stories, so "
      + "they are not in this file. Everything else — bookmarks, progress, "
      + "preferences — is here.")
    return []
  }
}

/** Put exported works back. Never overwrites: a restore is a merge, so a
 *  reader who has re-downloaded a work since the export keeps the newer copy. */
export async function importOfflineStories(
  stories: OfflineStory[],
): Promise<{ restored: number; skipped: number }> {
  if (!stories.length) return { restored: 0, skipped: 0 }
  const db = await openDB()
  return new Promise((resolve, reject) => {
    let restored = 0, skipped = 0
    const tx = db.transaction(STORE, "readwrite")
    const store = tx.objectStore(STORE)
    for (const story of stories) {
      // The writer's own guard, so a malformed file cannot put a record the rest
      // of the module will choke on later — and the comment that used to claim
      // this guard existed here while it existed nowhere was itself the bug.
      if (!isUsableRecord(story)) { skipped++; continue }
      const existing = store.get(story.id)
      existing.onsuccess = () => {
        if (existing.result) { skipped++; return }
        store.put(story); restored++
      }
    }
    tx.oncomplete = () => { db.close(); resolve({ restored, skipped }) }
    tx.onerror = () => {
      db.close()
      const e = tx.error
      reject(e?.name === "QuotaExceededError"
        ? new Error("This device does not have room for the saved works in that "
                  + "file. Remove some from your Library's Offline tab and try again.")
        : e)
    }
  })
}


/**
 * The story-page view of a saved copy, for a reader with no connection.
 *
 * Lives here rather than inline in the story page so it can be tested: the bug it
 * replaces was a mapping inside a React effect, which is exactly where a wrong
 * constant goes unnoticed — nothing throws, the page renders, and every value is
 * quietly false.
 *
 * The rule it follows is that a missing field must not become an invented one. A
 * saved record either carries a value or it does not, and "does not" has to render
 * as absent. The previous version substituted `status: "unknown"`, `kudos: 0`,
 * `language: "English"` and empty tag arrays for every record regardless of
 * contents, which on a work with 1,039 kudos and 59,744 hits produced a page with
 * no kudos row (the render hides anything `> 0`), and "Not stated" in place of
 * Complete.
 *
 * The defaults below exist only for records written before v3, which genuinely
 * lack the fields, so an un-re-saved story looks exactly as it did before.
 */
export function savedStoryToDetail(saved: OfflineStory) {
  return {
    ...saved,
    // What this device holds, and what the work actually has. Rendered as
    // `4/199`, so a partial save says so instead of showing "4/?".
    chapter_count: saved.chapters.length,
    chapter_count_total: saved.chapter_count_total ?? undefined,
    chapters: saved.chapters.map(c => ({
      id: `${saved.id}-${c.number}`,
      number: c.number,
      title: c.title,
      word_count: 0,   // per-chapter length is not stored; the story total is
    })),
    fandoms: saved.fandoms ?? [],
    relationships: saved.relationships ?? [],
    characters: saved.characters ?? [],
    tags: saved.tags ?? [],
    warnings: saved.warnings ?? [],
    categories: saved.categories ?? [],
    status: saved.status ?? "unknown",
    language: saved.language ?? "English",
    kudos: saved.kudos ?? 0,
    hits: saved.hits ?? 0,
    bookmarks: saved.bookmarks ?? 0,
    comments: saved.comments ?? 0,
    rating: saved.rating,
    updated_at: saved.updated_at,
    is_hosted: true,
    word_count: saved.word_count ?? 0,
  }
}

export async function getOfflineStory(id: string): Promise<OfflineStory | null> {
  try {
    const db = await openDB()
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, "readonly")
      const req = tx.objectStore(STORE).get(id)
      req.onsuccess = () => { db.close(); resolve(req.result ?? null) }
      req.onerror = () => { db.close(); reject(req.error) }
    })
  } catch {
    return null
  }
}

export async function listOfflineStories(): Promise<OfflineStory[]> {
  try {
    const db = await openDB()
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, "readonly")
      const req = tx.objectStore(STORE).getAll()
      req.onsuccess = () => {
        db.close()
        const all = (req.result ?? []) as OfflineStory[]
        all.sort((a, b) => (b.savedAt || "").localeCompare(a.savedAt || ""))
        resolve(all)
      }
      req.onerror = () => { db.close(); reject(req.error) }
    })
  } catch {
    return []
  }
}

export async function deleteOfflineStory(id: string, keepOnShelf = false): Promise<void> {
  // Removing a download is not the same as changing your mind about wanting the
  // work with you. The shelf entry goes too by default — an explicit "remove"
  // means both — but callers clearing space can keep the choice.
  if (!keepOnShelf) removeFromShelf(id)
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite")
    tx.objectStore(STORE).delete(id)
    tx.oncomplete = () => { db.close(); resolve() }
    tx.onerror = () => { db.close(); reject(tx.error) }
  })
}

export async function isStoryOffline(id: string): Promise<boolean> {
  const s = await getOfflineStory(id)
  return s != null
}

// Convenience: fetch every chapter of a story from the API and persist it for
// offline reading. Returns the number of chapters saved. Caller handles errors
// and progress UI.
export async function downloadStoryForOffline(
  storyId: string,
  onProgress?: (done: number, total: number) => void,
): Promise<number> {
  // Ask for persistent storage on the first save. Doing it here rather than at
  // app start matters: Chrome weighs engagement signals, and a request made at
  // the moment the reader deliberately saves something is both more likely to be
  // granted and easier to explain if it is not.
  await requestPersistentStorage()

  const metaRes = await fetchWithTimeout(`/api/stories/${storyId}`,
    {}, USER_TIMEOUT_MS)
  if (!metaRes.ok) throw new Error(`Couldn't load story (HTTP ${metaRes.status})`)
  const meta = await metaRes.json()

  // Refuse up front rather than partway through.
  //
  // Nothing checked the quota, so saving a long work on a nearly-full device
  // failed on whichever chapter happened to cross the limit — after minutes of
  // downloading, with a QuotaExceededError the UI reported as "Couldn't save
  // offline: undefined". Estimating first turns that into one honest sentence
  // with real numbers, before any work is done.
  //
  // The estimate is deliberately generous: word_count is the only size signal
  // available before downloading, HTML markup roughly doubles the plain text,
  // and IndexedDB stores strings as UTF-16. ~6 bytes per word is conservative
  // and errs toward warning rather than toward failing halfway.
  const est = await storageEstimate()
  if (est) {
    const needed = Math.max(1, (meta.word_count || 0)) * 6
    if (est.quota > 0 && needed > est.available) {
      throw new Error(
        `Not enough space on this device: this story needs about `
        + `${fmtBytes(needed)} and only ${fmtBytes(est.available)} is free. `
        + `Remove a saved story from your Library's Offline tab and try again.`)
    }
  }

  // Iterate the chapters the story ACTUALLY has, not its declared chapter_count.
  // Those disagree on real rows (a story can claim 50 chapters while 30 are
  // stored), and looping 1..chapter_count then threw on the first missing number
  // — aborting the whole save, so the story ended up not available offline at
  // all. Chapter numbers are also not guaranteed contiguous.
  const numbers: number[] = Array.isArray(meta.chapters) && meta.chapters.length
    ? meta.chapters.map((c: any) => c.number).sort((a: number, b: number) => a - b)
    : Array.from({ length: meta.chapter_count || 1 }, (_, i) => i + 1)

  const total = numbers.length
  const chapters: OfflineChapter[] = []
  // Anything already downloaded on a previous, interrupted attempt. Resuming
  // costs one lookup and saves refetching a hundred chapters over a connection
  // that has just proved unreliable.
  const existing = await getOfflineStory(storyId).catch(() => null)
  const have = new Map((existing?.chapters ?? []).map(c => [c.number, c]))
  const missing: number[] = []
  // Chapters the archive answered 404 for. Separate from `missing` because they
  // are not retryable — see `absentChapters` on the interface for why folding
  // them together costs a full re-download on every page load.
  //
  // Seeded from the existing record so a chapter that 404s once stays recorded
  // across attempts instead of being re-requested on every resume.
  const absent: number[] = [...(existing?.absentChapters ?? [])]
  for (const [i, n] of numbers.entries()) {
    const already = have.get(n)
    if (already && already.content && already.content.trim()) {
      chapters.push(already)
      onProgress?.(i + 1, total)
      continue
    }
    // Already known to be gone from the archive. The seeding above promised
    // this — "instead of being re-requested on every resume" — and nothing
    // kept the promise: the chapter was fetched again, 404'd again, and was
    // pushed a SECOND time, so one deleted chapter read as two holes.
    if (absent.includes(n)) {
      onProgress?.(i + 1, total)
      continue
    }
    // Wait out our own rate limiter rather than failing the download.
    //
    // Saving a long work means fetching every chapter as fast as the loop can
    // go, and the read limit is 300 a minute — so a 199-chapter story reliably
    // hit 429 partway through and threw, discarding everything fetched so far.
    // Measured: "Chapter 166 failed (HTTP 429)" and nothing saved at all.
    //
    // This is a reader downloading a work they are already allowed to read, one
    // request at a time, so waiting is the correct response to being asked to
    // slow down — not abandoning. Retry-After is honoured when sent, and the
    // backoff is capped so a genuinely stuck server still ends the attempt
    // rather than hanging forever.
    let r: Response | null = null
    let lostConnection = false
    for (let attempt = 0; attempt < 6; attempt++) {
      try {
        // DOWNLOAD_TIMEOUT_MS, not USER_TIMEOUT_MS. This loop honours 429 and
        // 5xx with backoff on purpose — waiting when the server asks is correct
        // — and a long chapter over a slow connection is a success rather than a
        // stall. Cutting it off would throw away minutes of downloading and, with
        // it, the partial save the catch below goes to some trouble to keep.
        r = await fetchWithTimeout(`/api/stories/${storyId}/chapters/${n}`,
          {}, DOWNLOAD_TIMEOUT_MS)
      } catch {
        // The connection went away mid-download. Everything fetched so far is
        // still good and still worth keeping — see the partial save below.
        lostConnection = true
        break
      }
      if (r.status !== 429 && r.status < 500) break
      const retryAfter = Number(r.headers.get("Retry-After"))
      const waitMs = Math.min(
        (Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : 2 ** attempt) * 1000,
        30_000)
      onProgress?.(i, total)          // keep the UI honest while we wait
      await new Promise(res => setTimeout(res, waitMs))
    }
    if (lostConnection) {
      // Stop here and record the rest as missing, rather than failing the whole
      // save. A story downloaded to chapter 150 of 199 is readable to chapter
      // 150; discarding it because the train went into a tunnel is the wrong
      // trade every time.
      missing.push(...numbers.slice(i))
      break
    }
    if (!r || !r.ok) {
      // Skip a chapter we can't fetch rather than losing the whole download.
      // A partially saved story is far more useful than nothing.
      //
      // A 404 is recorded as ABSENT as well as skipped, and that second half is
      // the fix. It used to `continue` alone, so the chapter was dropped and
      // never recorded anywhere, which made a story with holes in it
      // indistinguishable from a complete one — and that is wrong in four
      // separate places at once:
      //
      //   * the record had no `missingChapters` at all, so the story page showed
      //     "✓ Saved offline" rather than "◐ Saved, N to go";
      //   * the chapter list had a hole in it with nothing marking it;
      //   * the shelf row had no incomplete state to show;
      //   * and Next simply skipped the hole — chapter 3 went to chapter 5 —
      //     which is the one a reader notices, and notices as a bug in the
      //     archive rather than in here.
      //
      // `absent` and not `missing`, because recording it as retryable is worse
      // than recording nothing at all: `resumeInterruptedDownloads` re-downloads
      // a whole work whenever `missingChapters` is non-empty, so a permanently
      // absent chapter would mean 199 requests on every library open forever, in
      // pursuit of a chapter the archive deleted. The reader's count still
      // includes it — see `StoryClient` — because from where they sit a hole is
      // a hole.
      if (r && r.status === 404) { absent.push(n); continue }
      throw new Error(`Chapter ${n} failed (HTTP ${r ? r.status : "no response"})`)
    }
    const ch = await r.json()
    chapters.push({
      number: n,
      title: ch.title,
      content: ch.content || "",
      start_note: ch.start_note,
      end_note: ch.end_note,
      summary: ch.summary,
    })
    onProgress?.(i + 1, total)
  }
  if (chapters.length === 0) {
    throw new Error("No chapters could be downloaded for this story.")
  }

  // Measured, not estimated. The reader needs to be able to see which saved
  // story is worth removing when space runs short, and a per-story figure is the
  // only thing that answers that. UTF-16 in IndexedDB, hence the doubling.
  const bytes = chapters.reduce(
    (n, c) => n + 2 * ((c.content?.length ?? 0) + (c.start_note?.length ?? 0)
                     + (c.end_note?.length ?? 0) + (c.summary?.length ?? 0)), 0)

  await saveStoryOffline({
    id: meta.id,
    title: meta.title,
    author: meta.author,
    site: meta.site,
    url: meta.url,
    summary: meta.summary,
    fandoms: meta.fandoms,
    word_count: meta.word_count,
    chapter_count: chapters.length,   // what we actually hold, not what was claimed
    // The work's real length, kept separately. The story page renders
    // `chapter_count/chapter_count_total`, so without this a partial save of a
    // 199-chapter work reads "4/?" — true, and useless to somebody deciding
    // whether their download is worth keeping.
    chapter_count_total: meta.chapter_count ?? undefined,
    chapters,
    bytes,
    schema: SCHEMA_VERSION,
    savedAt: new Date().toISOString(),
    // The metadata the story page shows, taken from the response already fetched
    // above. See the v3 note on OfflineStory for why a missing value must not be
    // replaced with a fabricated one at read time.
    status: meta.status ?? undefined,
    rating: meta.rating ?? undefined,
    kudos: meta.kudos ?? undefined,
    hits: meta.hits ?? undefined,
    bookmarks: meta.bookmarks ?? undefined,
    comments: meta.comments ?? undefined,
    language: meta.language ?? undefined,
    published_at: meta.published_at ?? undefined,
    updated_at: meta.updated_at ?? undefined,
    relationships: meta.relationships ?? [],
    characters: meta.characters ?? [],
    tags: meta.tags ?? [],
    warnings: meta.warnings ?? [],
    categories: meta.categories ?? [],
    // How many chapters had text when this was written. The distinction that
    // matters later is "became empty" (evicted) versus "was always empty" — a
    // real chapter can legitimately hold no text, and treating those as damage
    // rejects perfectly good downloads.
    textChapters: chapters.filter(c => c.content && c.content.trim()).length,
    // Chapters still to fetch, if the connection went away part-way. Their
    // presence is what makes this record resumable rather than merely
    // incomplete.
    //
    // Retryable only. A chapter the archive does not have is recorded separately
    // below precisely so that its presence does NOT make this record resumable.
    missingChapters: missing.length ? missing : undefined,
    // Permanently unavailable, and deliberately not a reason to resume. A record
    // with only these is as complete as it will ever be, so `missing` stays
    // undefined and the work stops being re-downloaded on every page load.
    absentChapters: absent.length ? [...new Set(absent)].sort((x, y) => x - y) : undefined,
  })

  // Read it back before claiming success.
  //
  // An IndexedDB write can resolve and still leave nothing usable: the quota can
  // be hit between the estimate and the commit, a transaction can be aborted by
  // the browser reclaiming space, and Safari can evict mid-session. All of those
  // end with the UI saying "Saved for offline" and the reader finding a blank
  // page on a train, which is the worst possible moment to discover it.
  //
  // Reading the row back costs one transaction and turns a silent failure into
  // an error the reader sees while they still have a connection to fix it.
  const expectedText = chapters.filter(c => c.content && c.content.trim()).length
  const verify = await getOfflineStory(storyId)
  const storedText = verify?.chapters?.filter(c => c.content && c.content.trim()).length ?? 0
  // Readable, not perfect. The first version of this demanded that EVERY stored
  // chapter have text, which deleted any story containing a legitimately empty
  // one — an author's note, a placeholder, a chapter whose text this index does
  // not hold. Those saved fine before and are worth keeping. What the readback
  // is actually for is catching a write that did not survive, so it compares
  // against what was just downloaded.
  const ok = !!verify && (verify.chapters?.length ?? 0) === chapters.length
             && storedText >= expectedText
             && chapters.length > 0
  if (ok) {
    // Only after the readback proves it is there. Putting a work on a shelf that
    // syncs to other devices when the download actually failed would spread the
    // failure rather than the story.
    addToShelf({ id: storyId, title: meta.title, author: meta.author })
  }
  if (!ok) {
    await deleteOfflineStory(storyId).catch(() => {})
    throw new Error(
      "Saved, but it could not be read back — this device may be out of space. "
      + "Nothing was kept, so try again after removing a saved story.")
  }
  // Cache the reader shell so this story opens offline with no prior online
  // visit. The service worker serves one cached reader shell for ANY
  // /story/.../chapter/... URL, so fetching one chapter page is enough — but we
  // do it on every save so it's always present. We're online here (the fetches
  // above just succeeded), so this fetch will hit the network and the SW caches
  // it. Find the current shell cache dynamically so this never goes stale when
  // the SW cache version is bumped.
  try {
    if (typeof caches !== "undefined") {
      const keys = await caches.keys()
      // SORTED, and it has to be the newest generation. `caches.keys()` returns
      // CacheStorage's own order, which is creation order and NOT specified by
      // the API — so with two generations retained this could pick the PREVIOUS
      // one and prime story shells into a cache that the service worker's
      // `activate` is about to delete, moments later. The symptom would be a
      // saved story whose page does not open offline, on a device that had just
      // been updated, which is exactly when somebody is checking whether the
      // update worked. `previousCacheName` in the worker sorts too; this is the
      // same rule in the second place it is needed.
      // Stamped names only: a pre-August `ficatlas-shell-v8` sorts above every
      // "2026…" stamp as a string, and would win this pick. See
      // sortGenerations in sw.template.js.
      const shell = keys.filter(k => /^ficatlas-shell-\d{14}$/.test(k)).sort().pop()
      if (shell) {
        const cache = await caches.open(shell)
        // Both routes: the reader AND the story detail page. Caching only the
        // reader meant a saved story's own page fell through to the generic
        // offline screen, so you could read it but not get to it.
        const first = chapters[0]?.number ?? 1
        for (const u of [`/story/${storyId}`, `/story/${storyId}/chapter/${first}`]) {
          try {
            const res = await fetchWithTimeout(u, {}, BACKGROUND_TIMEOUT_MS)
            if (res.ok) await cache.put(u, res.clone())
          } catch {}
        }
      }
    }
  } catch {}

  return chapters.length
}
