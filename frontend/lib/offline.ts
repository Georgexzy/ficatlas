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
}

const DB_NAME = "ficatlas-offline"
const DB_VERSION = 1
const STORE = "stories"

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("IndexedDB not available"))
      return
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "id" })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

export async function saveStoryOffline(story: OfflineStory): Promise<void> {
  const db = await openDB()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite")
    tx.objectStore(STORE).put(story)
    tx.oncomplete = () => { db.close(); resolve() }
    tx.onerror = () => { db.close(); reject(tx.error) }
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
  const metaRes = await fetch(`/api/stories/${storyId}`)
  if (!metaRes.ok) throw new Error(`Couldn't load story (HTTP ${metaRes.status})`)
  const meta = await metaRes.json()

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
    savedAt: new Date().toISOString(),
  })
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
