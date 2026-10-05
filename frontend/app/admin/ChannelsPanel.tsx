"use client"

import { useCallback, useEffect, useState } from "react"
import { fetchWithTimeout, USER_TIMEOUT_MS } from "@/lib/net"

/**
 * Outreach that is not a reply: the researched channels, and this week's
 * digest. See backend/api/outreach.py for the research behind each line.
 *
 * Every channel is something a person does, once and well. The status is
 * there so a decision is made once — "not doing AO3 comments" is a status,
 * not something to re-argue every month.
 */

interface Channel {
  id: string; name: string; url: string
  why: string; how: string; avoid: string
  status: "todo" | "doing" | "done" | "skip"; note: string | null; at: number | null
}
interface Digest {
  days: number
  works: { id: string; title: string; author: string | null; fandom: string | null; people: number; url: string }[]
  bluesky: string[]
  tumblr: string | null
}

const STATUS_LABEL = { todo: "To do", doing: "In progress", done: "Done", skip: "Not doing" }

export default function ChannelsPanel() {
  const [channels, setChannels] = useState<Channel[] | null>(null)
  const [digest, setDigest] = useState<Digest | null>(null)
  const [days, setDays] = useState(7)
  const [copied, setCopied] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    fetchWithTimeout("/api/outreach/channels", { credentials: "include" }, USER_TIMEOUT_MS)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(setChannels).catch(e => setErr(String(e.message || e)))
  }, [])

  const loadDigest = useCallback(async (d: number) => {
    setDigest(null)
    try {
      const r = await fetchWithTimeout(`/api/outreach/digest?days=${d}`,
        { credentials: "include" }, USER_TIMEOUT_MS)
      if (r.ok) setDigest(await r.json())
    } catch { /* the section says it has nothing */ }
  }, [])
  useEffect(() => { loadDigest(days) }, [days, loadDigest])

  const setStatus = async (id: string, status: Channel["status"]) => {
    setChannels(cs => cs?.map(c => c.id === id ? { ...c, status } : c) ?? null)
    await fetchWithTimeout(`/api/outreach/channels/${id}`, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    }, USER_TIMEOUT_MS).catch(() => {})
  }

  const copy = async (what: string, textOut: string) => {
    try { await navigator.clipboard.writeText(textOut); setCopied(what) }
    catch { setCopied(null) }
    setTimeout(() => setCopied(null), 2000)
  }

  const done = channels?.filter(c => c.status === "done" || c.status === "skip").length ?? 0

  return (
    <section className="channels" aria-labelledby="channels-h">
      <h2 id="channels-h" className="admin-section__title">
        Other ways to reach readers
        {channels && <span className="channels__progress">{done} of {channels.length} decided</span>}
      </h2>
      {err && <p className="settings-save-error" role="alert">{err}</p>}

      <ul className="channels__list">
        {(channels ?? []).map(c => (
          <li key={c.id} className={`channel channel--${c.status}`}>
            <div className="channel__head">
              <a href={c.url} target="_blank" rel="noopener noreferrer" className="channel__name">{c.name} ↗</a>
              <select value={c.status} aria-label={`Status for ${c.name}`}
                onChange={e => setStatus(c.id, e.target.value as Channel["status"])}>
                {(Object.keys(STATUS_LABEL) as Channel["status"][]).map(s => (
                  <option key={s} value={s}>{STATUS_LABEL[s]}</option>
                ))}
              </select>
            </div>
            <p className="channel__why">{c.why}</p>
            <details>
              <summary>How, and what to avoid</summary>
              <p><strong>How:</strong> {c.how}</p>
              <p><strong>Avoid:</strong> {c.avoid}</p>
            </details>
          </li>
        ))}
      </ul>

      <h3 className="channels__sub">
        Most recommended
        <select value={days} onChange={e => setDays(Number(e.target.value))} aria-label="Window">
          <option value={7}>this week</option>
          <option value={30}>last 30 days</option>
          <option value={90}>last 90 days</option>
        </select>
      </h3>
      <p className="admin-note">
        Works at least two different people recommended in the fic communities
        the worker reads, with both content tiers filtered out — this is a
        public post. Drafted for a weekly Bluesky thread and a Tumblr post.
      </p>
      {!digest && <p className="loading">Counting…</p>}
      {digest && digest.works.length === 0 && (
        <p className="queue__note">
          Nobody has been recommended by two people in this window yet. Try a
          longer one; a quiet week is better skipped than filled.
        </p>
      )}
      {digest && digest.works.length > 0 && (
        <div className="channels__digest">
          <ol className="channels__works">
            {digest.works.map(w => (
              <li key={w.id}>
                <a href={`/story/${w.id}`}>{w.title}</a>
                {w.author && <> by {w.author}</>}
                {w.fandom && <span className="channels__fandom"> · {w.fandom}</span>}
                <span className="channels__people"> · {w.people} people</span>
              </li>
            ))}
          </ol>
          <div className="outreach__row">
            <button className="btn btn--primary"
              onClick={() => copy("Bluesky thread", digest.bluesky.join("\n\n---\n\n"))}>
              Copy Bluesky thread ({digest.bluesky.length} posts)
            </button>
            {digest.tumblr && (
              <button className="btn" onClick={() => copy("Tumblr post", digest.tumblr!)}>
                Copy Tumblr post
              </button>
            )}
            {copied && <span className="outreach__copied">Copied {copied}</span>}
          </div>
          <details>
            <summary>Preview the thread</summary>
            {digest.bluesky.map((p, i) => <pre key={i} className="outreach__reply">{p}</pre>)}
          </details>
        </div>
      )}
    </section>
  )
}
