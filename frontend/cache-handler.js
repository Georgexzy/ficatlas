// In-memory incremental cache for `next start` in a read-only container.
//
// The container runs with `read_only: true` and a tmpfs only over
// /app/.next/cache (docker-compose.public.yml). Next's DEFAULT cache handler
// keeps two things in two places: fetch results under .next/cache — writable,
// fine — and rendered ISR/force-static HTML next to the build output under
// .next/server/app, which is on the read-only image layer. So every render of a
// `force-static` page ended in
//
//   Failed to update prerender cache for /s/<code>
//   [Error: EROFS: read-only file system, open '/app/.next/server/app/s/<code>.html']
//
// and, because the write is what populates the cache, the page re-rendered from
// scratch on every single request. /s/<code> is the short link on ~750k story
// pages and the surface crawlers walk, so this was the hot path.
//
// The obvious fixes are both wrong. A tmpfs over .next/server/app SHADOWS the
// compiled route files that live there. A named volume would work exactly once:
// Docker seeds a volume from the image only when the volume is empty, so the
// next promote would serve the previous build's JS forever.
//
// So: hold the cache in memory instead. Nothing to write, nothing to shadow,
// and it survives exactly as long as the process that owns it — which is the
// right lifetime here anyway, since promote.sh replaces the container on every
// deploy and a cache that outlived the build would be a bug, not a feature.
//
// The trade is that the two colours, and any future replicas, each keep their
// own copy. That is acceptable: entries are cheap to regenerate and revalidate
// is time-based, so the only cost of a miss is one render.

const DEFAULT_MAX_ENTRIES = Number(process.env.ISR_CACHE_MAX_ENTRIES || 2000)

// Module scope, not instance scope. Next constructs the handler more than once,
// and a cache per instance would mean a cache that never hits.
const store = new Map()
const tagIndex = new Map() // tag -> Set(key)

function evictIfNeeded() {
  // Map preserves insertion order, so the first key is the oldest write. Plain
  // FIFO rather than LRU: with a time-based revalidate the age of an entry is
  // what matters, and an LRU would need a touch on every read to no real gain.
  while (store.size > DEFAULT_MAX_ENTRIES) {
    const oldest = store.keys().next()
    if (oldest.done) break
    dropKey(oldest.value)
  }
}

function dropKey(key) {
  const entry = store.get(key)
  if (entry) {
    for (const tag of entry.tags || []) {
      const keys = tagIndex.get(tag)
      if (keys) {
        keys.delete(key)
        if (keys.size === 0) tagIndex.delete(tag)
      }
    }
  }
  store.delete(key)
}

module.exports = class InMemoryCacheHandler {
  constructor(options) {
    this.options = options
  }

  async get(key) {
    const entry = store.get(key)
    if (!entry) return null
    return { value: entry.value, lastModified: entry.lastModified }
  }

  async set(key, value, ctx) {
    // Normalise the tags across the shapes Next has used for them, so a version
    // bump cannot quietly turn revalidateTag into a no-op.
    const tags = (ctx && (ctx.tags || (ctx.softTags ?? []))) || []
    if (store.has(key)) dropKey(key)
    store.set(key, { value, lastModified: Date.now(), tags })
    for (const tag of tags) {
      if (!tagIndex.has(tag)) tagIndex.set(tag, new Set())
      tagIndex.get(tag).add(key)
    }
    evictIfNeeded()
  }

  async revalidateTag(tags) {
    const list = Array.isArray(tags) ? tags : [tags]
    for (const tag of list) {
      const keys = tagIndex.get(tag)
      if (!keys) continue
      for (const key of Array.from(keys)) dropKey(key)
    }
  }

  // Called between requests when Next wants per-request memoisation cleared.
  // This cache is deliberately process-wide, so there is nothing to reset.
  resetRequestCache() {}
}
