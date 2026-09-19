"use client"

import { useCallback, useEffect, useState } from "react"

/**
 * The fic-finder posts waiting for an answer.
 *
 * The outreach panel turns a post into a search in seconds; finding the posts
 * was still somebody remembering to look, and the measurement said that was
 * the binding constraint — Reddit sent SEVEN sessions in thirty days, against
 * eighty from Google, on a site whose whole thesis is that those threads are
 * the audience already asking the question it answers.
 *
 * So this is the worklist, fetched and read offline by reddit_queue.py. It
 * posts nothing anywhere: clicking through opens Reddit in a tab, the reply is
 * written next door in Outreach, and a person sends it.
 *
 * Ordered by whether there is an answer and then by how FEW works it takes to
 * give it, because a post resolving to four works can be answered and one
 * resolving to five thousand cannot.
 */
interface Post {
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
  return h < 1 ? "just now" : h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`
}

export default function QueuePanel() {
  const [posts, setPosts] = useState<Post[] | null>(null)
  const [state, setState] = useState<"new" | "answered" | "skipped">("new")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async (s: string) => {
    setError(null); setPosts(null)
    try {
      const r = await fetch(`/api/queue?state=${s}`, { credentials: "include" })
      if (!r.ok) throw new Error(`Could not load the queue (${r.status}).`)
      setPosts(await r.json())
    } catch (e: any) { setError(e.message) }
  }, [])

  useEffect(() => { load(state) }, [state, load])

  const mark = async (id: string, to: string) => {
    setBusy(id)
    try {
      await fetch(`/api/queue/${encodeURIComponent(id)}/state`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: to }),
      })
      setPosts(p => (p ?? []).filter(x => x.id !== id))
    } catch { /* leaving the row up is the honest failure */ }
    finally { setBusy(null) }
  }

  return (
    <>
      <h1 className="settings-title">Posts to answer</h1>
      <p className="admin-note">
        Fetched from the Lost Fic and Fic Search flairs and read through the
        same extractor the Outreach tab uses. Nothing is posted anywhere —
        open the post, write the reply next door, and send it yourself.
      </p>

      <div className="admin-tabs">
        {(["new", "answered", "skipped"] as const).map(s => (
          <button key={s} onClick={() => setState(s)}
            className={`library-tab ${state === s ? "library-tab--on" : ""}`}>
            {s === "new" ? "Waiting" : s === "answered" ? "Answered" : "Skipped"}
          </button>
        ))}
      </div>

      {error && <p className="settings-save-error" role="alert">{error}</p>}
      {!posts && !error && <p className="loading">Reading the queue…</p>}
      {posts && posts.length === 0 && (
        <p className="admin-note">
          Nothing here. The fetcher runs on a timer and is deliberately slow —
          Reddit rate-limits an unauthenticated feed after one request, so a
          run takes a couple of minutes and covers one subreddit at a time.
        </p>
      )}

      <ul className="queue-list">
        {(posts ?? []).map(p => (
          <li key={p.id} className="queue-item">
            <div className="queue-item__head">
              <a href={p.url} target="_blank" rel="noopener noreferrer"
                 className="queue-item__title">{p.title}</a>
              {/* The count is the whole ranking, so it leads. A post we cannot
                  answer says so rather than showing a zero that reads as a
                  loading state. */}
              <span className={`queue-item__works ${
                p.link_unsafe ? "queue-item__works--unsafe"
                : p.works ? "queue-item__works--yes" : "queue-item__works--no"}`}>
                {p.link_unsafe ? "rules forbid the link"
                  : p.works ? `${p.works.toLocaleString()}${p.works >= 2000 ? "+" : ""} works`
                  : "no answer yet"}
              </span>
            </div>
            <p className="queue-item__meta">
              r/{p.subreddit} · {AGO(p.posted_at)}
            </p>
            {p.query && <p className="queue-item__query">{p.query}</p>}
            {p.body && <p className="queue-item__body">{p.body.slice(0, 260)}</p>}
            {state === "new" && (
              <div className="queue-item__acts">
                <button disabled={busy === p.id}
                  onClick={() => mark(p.id, "answered")}>Answered</button>
                <button disabled={busy === p.id} className="queue-item__skip"
                  onClick={() => mark(p.id, "skipped")}>Skip</button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </>
  )
}
