// Offline story storage backed by IndexedDB.
//
// Why IndexedDB and not localStorage: full chapter text for a long fic can be
// several megabytes — well past localStorage's ~5MB total budget. IndexedDB has
// no practical cap for our purposes and stores structured data directly.
//
// Layout: one object store "stories" keyed by story id. Each record holds the
// story metadata plus an array of {number, title, content} chapters, so a saved
// story is fully self-contained and readable with no network.

export interface OfflineChapter {
  number: number
  title?: string
  content: string
  start_note?: string
  end_note?: string
  summary?: string
}

export interface OfflineStory {
  id: string
  title: string
  author: string
  site: string
  url: string
  summary?: string
  fandoms?: string[]
  word_count?: number
  chapter_count: number
  chapters: OfflineChapter[]
  savedAt: string   // ISO timestamp
  /** Approximate bytes this record occupies. Absent on records written by v1. */
  bytes?: number
  /** Schema version that wrote this record, so migrations can be selective. */
  schema?: number
}

/** Bumped whenever the record shape changes; written into each record. */
export const SCHEMA_VERSION = 2

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
    // A chapter row with no text is a half-written save or a partially evicted
    // one; either way it will read as a blank page offline.
    const empty = chapters.filter(c => !c.content || !c.content.trim()).length
    if (empty) {
      out.broken.push({ id: story.id, title: story.title,
                        reason: `${empty} of ${chapters.length} chapters are empty` })
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
    detail: `This site is being served over http://${window.location.host}. `
      + `Browsers only allow offline caching on https (or localhost), so the app `
      + `itself cannot be stored for offline use. Saved stories are still on this `
      + `device, but opening the app with no connection will show nothing.`,
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

export async function saveStoryOffline(story: OfflineStory): Promise<void> {
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

export async function deleteOfflineStory(id: string): Promise<void> {
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

  const metaRes = await fetch(`/api/stories/${storyId}`)
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
  for (const [i, n] of numbers.entries()) {
    const r = await fetch(`/api/stories/${storyId}/chapters/${n}`)
    if (!r.ok) {
      // Skip a chapter we can't fetch rather than losing the whole download.
      // A partially saved story is far more useful than nothing.
      if (r.status === 404) continue
      throw new Error(`Chapter ${n} failed (HTTP ${r.status})`)
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
    chapters,
    bytes,
    schema: SCHEMA_VERSION,
    savedAt: new Date().toISOString(),
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
  const verify = await getOfflineStory(storyId)
  if (!verify || !verify.chapters?.length
      || verify.chapters.some(c => !c.content || !c.content.trim())) {
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
      const shell = keys.find(k => k.startsWith("ficatlas-shell-"))
      if (shell) {
        const cache = await caches.open(shell)
        // Both routes: the reader AND the story detail page. Caching only the
        // reader meant a saved story's own page fell through to the generic
        // offline screen, so you could read it but not get to it.
        const first = chapters[0]?.number ?? 1
        for (const u of [`/story/${storyId}`, `/story/${storyId}/chapter/${first}`]) {
          try {
            const res = await fetch(u)
            if (res.ok) await cache.put(u, res.clone())
          } catch {}
        }
      }
    }
  } catch {}

  return chapters.length
}
