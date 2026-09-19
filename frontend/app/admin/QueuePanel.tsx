"use client"

import { useCallback, useEffect, useState } from "react"

/**
 * The fic-finder posts waiting for an answer — the left half of Outreach.
 *
 * It used to be a tab of its own beside the finder, which made answering a
 * post a four-step tab dance: open it on Reddit, select the text, switch tab,
 * paste. The two are one workflow, so they are now one screen: pick a post
 * here and the finder next door has already read it.
 *
 * Ordering is the whole feature. ANSWERABLE first — fewest works, because four
 * can be read and five thousand cannot — with NEWEST available for when a
 * thread is live and arriving late is the same as not arriving.
 */
export interface Post {
  id: string
  subreddit: string
  title: string
  body?: string | null
  url: string
  posted_at?: string | null
  query?: string | null
  works?: number | null
  link_unsafe: boolean
  state: string
}

const AGO = (iso?: string | null) => {
  if (!iso) return ""
  const h = Math.round((Date.now() - Date.parse(iso)) / 3_600_000)
  if (!isFinite(h)) return ""
  return h < 1 ? "just now" : h < 24 ? `${h}h` : `${Math.round(h / 24)}d`
}

export default function QueuePanel(
  { selectedId, onPick, onCount, onList, onDone }: {
    selectedId?: string | null
    onPick: (p: Post) => void
    onCount?: (n: number) => void
    /** The list as loaded, so the pane next door can advance to the next post
     *  without waiting for a round trip. */
    onList?: (ps: Post[]) => void
    /** A post was marked from in here, so the work pane should move on too. */
    onDone?: (id: string) => void
  },
) {
  const [posts, setPosts] = useState<Post[] | null>(null)
  const [state, setState] = useState<"new" | "answered" | "skipped">("new")
  const [order, setOrder] = useState<"answerable" | "newest">("answerable")
  const [sub, setSub] = useState("")
  const [search, setSearch] = useState("")
  const [subs, setSubs] = useState<{ subreddit: string; waiting: number }[]>([])
  // How many are in each state, so the tabs carry a number — a worklist whose
  // size is only visible once you open it is one you forget to open.
  const [states, setStates] = useState<Record<string, number>>({})
  const [error, setError] = useState<string | null>(null)
  const [fetching, setFetching] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const qs = new URLSearchParams({ state, order, subreddit: sub, search })
      const r = await fetch(`/api/queue?${qs}`, { credentials: "include" })
      if (!r.ok) throw new Error(`Could not load the queue (${r.status}).`)
      const data: Post[] = await r.json()
      setPosts(data)
      onList?.(data)
      if (state === "new") onCount?.(data.length)
    } catch (e: any) { setError(e.message) }
  }, [state, order, sub, search, onCount])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    fetch("/api/queue/counts", { credentials: "include" })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) { setSubs(d.subreddits ?? []); setStates(d.states ?? {}) } })
      .catch(() => {})
  }, [posts])

  const mark = async (id: string, to: string) => {
    try {
      await fetch(`/api/queue/${encodeURIComponent(id)}/state`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: to }),
      })
      setPosts(p => {
        const next = (p ?? []).filter(x => x.id !== id)
        onList?.(next)
        if (state === "new") onCount?.(next.length)
        return next
      })
      // Only when the row being cleared is the one on screen — marking a
      // different post from the list should not move you off the one you are
      // in the middle of answering.
      if (id === selectedId) onDone?.(id)
    } catch { /* leaving the row up is the honest failure */ }
  }

  const fetchNow = async () => {
    setFetching("Fetching…")
    try {
      const r = await fetch("/api/queue/refresh",
        { method: "POST", credentials: "include" })
      const d = await r.json()
      // The fetch runs behind the response — a run walks the feeds with a
      // twenty-second gap, so holding the button would mean a two-minute
      // spinner for something the next load picks up anyway.
      setFetching(d.started
        ? "Fetching in the background — reload in a minute"
        : d.reason ?? "Not now")
    } catch { setFetching("Could not start a fetch") }
    setTimeout(() => setFetching(null), 8000)
  }

  return (
    <div className="queue">
      <div className="queue__bar">
        {(["new", "answered", "skipped"] as const).map(s => (
          <button key={s} onClick={() => setState(s)}
            className={`library-tab ${state === s ? "library-tab--on" : ""}`}>
            {s === "new" ? "Waiting" : s === "answered" ? "Answered" : "Skipped"}
            {states[s] ? <span className="queue__tab-n">{states[s]}</span> : null}
          </button>
        ))}
        <button className="queue__fetch" onClick={fetchNow}>Fetch now</button>
      </div>

      {fetching && <p className="queue__note">{fetching}</p>}

      <div className="queue__filters">
        <input className="queue__search" type="search" value={search}
          placeholder="Filter by words in the post"
          onChange={e => setSearch(e.target.value)} />
        <select value={sub} onChange={e => setSub(e.target.value)}>
          <option value="">All subreddits</option>
          {subs.map(s => (
            <option key={s.subreddit} value={s.subreddit}>
              r/{s.subreddit} ({s.waiting})
            </option>
          ))}
        </select>
        <select value={order} onChange={e => setOrder(e.target.value as any)}>
          <option value="answerable">Most answerable</option>
          <option value="newest">Newest first</option>
        </select>
      </div>

      {error && <p className="settings-save-error" role="alert">{error}</p>}
      {!posts && !error && <p className="loading">Reading the queue…</p>}
      {posts && posts.length === 0 && (
        <p className="queue__note">
          Nothing here. The fetcher runs hourly and is deliberately slow —
          Reddit rate-limits an unauthenticated feed after one request, so a run
          covers one subreddit at a time.
        </p>
      )}

      <ul className="queue-list">
        {(posts ?? []).map(p => (
          <li key={p.id}
            className={`queue-item ${selectedId === p.id ? "queue-item--on" : ""}`}>
            <button className="queue-item__pick" onClick={() => onPick(p)}>
              <span className="queue-item__head">
                <span className="queue-item__title">{p.title}</span>
                <span className={`queue-item__works ${
                  p.link_unsafe ? "queue-item__works--unsafe"
                  : p.works ? "queue-item__works--yes" : "queue-item__works--no"}`}>
                  {p.link_unsafe ? "rules forbid the link"
                    : p.works ? `${p.works.toLocaleString()}${p.works >= 2000 ? "+" : ""}`
                    : "no answer"}
                </span>
              </span>
              <span className="queue-item__meta">
                r/{p.subreddit} · {AGO(p.posted_at)}
                {/* TITLE ONLY, said rather than left to be discovered.
                    About a tenth of posts arrive with no body — the request is
                    entirely in the title, or it is a crosspost whose body is a
                    link wrapper. The extractor then has eight words to work
                    with instead of a paragraph, which is worth knowing BEFORE
                    you judge the search it produced. Reddit blocks the
                    unauthenticated JSON that would carry the rest (403), so
                    recovering it needs a registered app — the same thing that
                    would lift the feed's rate limit. */}
                {(p.body ?? "").trim().length < 40 && (
                  <span className="queue-item__thin" title="No body came through — the search is built from the title alone">
                    · title only
                  </span>
                )}
              </span>
            </button>
            <div className="queue-item__acts">
              <a href={p.url} target="_blank" rel="noopener noreferrer">Open on Reddit</a>
              {state === "new" && (
                <>
                  <button onClick={() => mark(p.id, "answered")}>Answered</button>
                  <button className="queue-item__skip"
                    onClick={() => mark(p.id, "skipped")}>Skip</button>
                </>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
