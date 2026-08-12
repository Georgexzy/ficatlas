"use client"
export const dynamic = "force-dynamic"

import { useEffect, useState, useCallback } from "react"
import Link from "next/link"
import SiteHeader from "../SiteHeader"
import { useAuth } from "@/lib/auth"
import { formatWordCount, chapterDisplay, SITE_LABELS } from "@/lib/api"

// The other half of following a work.
//
// The API for this was complete — list, unread count, mark-seen — and none of it
// had a caller. You could follow a work from its page and then never be told it
// had updated, which is the only reason anyone follows a work. /api/follows/count
// even documents itself as "drives the badge", for a badge that did not exist.
//
// Ordering comes from the server: works with new chapters first, then most
// recently updated. That is the order this page is read in — the question is
// "what has moved?", not "what am I following?", and the second question is
// answered by scrolling.

interface FollowedWork {
  id: string
  title: string
  author: string | null
  site: string
  chapter_count: number | null
  word_count: number | null
  status: string | null
  updated_at: string | null
  new_chapters: number
  is_new: boolean
}

function fmtDate(iso: string | null): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ""
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
}

export default function FollowsPage() {
  const { user, loading: authLoading } = useAuth()
  const [works, setWorks] = useState<FollowedWork[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/follows", { credentials: "include" })
      if (r.status === 401) { setWorks([]); return }
      if (!r.ok) throw new Error("Could not load your follows")
      setWorks(await r.json())
    } catch (e: any) {
      setError(e.message || "Could not load your follows")
    }
  }, [])

  useEffect(() => { if (!authLoading && user) load() }, [authLoading, user, load])

  // Marking seen is per-work rather than a single "clear all", because the list
  // is a to-read queue: clearing it wholesale is the one action nobody can undo
  // by remembering what was on it.
  const markSeen = async (id: string) => {
    setBusy(id)
    try {
      await fetch(`/api/follows/${id}/seen`, { method: "POST", credentials: "include" })
      setWorks(w => (w ?? []).map(x =>
        x.id === id ? { ...x, new_chapters: 0, is_new: false } : x))
    } finally {
      setBusy(null)
    }
  }

  const unfollow = async (id: string) => {
    setBusy(id)
    try {
      await fetch(`/api/follows/${id}`, { method: "DELETE", credentials: "include" })
      setWorks(w => (w ?? []).filter(x => x.id !== id))
    } finally {
      setBusy(null)
    }
  }

  const unread = (works ?? []).filter(w => w.is_new).length

  return (
    // page-prose is the shared wrapper for every non-search page (about,
    // takedown, permissions) and carries the header, the max-width and the
    // padding. --wide because this is a list of works rather than prose: at the
    // 660px reading measure the title and the actions collide.
    <div className="page-prose page-prose--wide">
      <SiteHeader />
      <main>
        <h1>Following</h1>

        {!authLoading && !user && (
          <p className="page-prose__muted">
            <Link href="/login?next=/follows">Sign in</Link> to follow works and be
            told when they update.
          </p>
        )}

        {user && works === null && !error && <p className="page-prose__muted">Loading…</p>}
        {error && <div className="alert alert--error" role="alert">{error}</div>}

        {user && works !== null && works.length === 0 && (
          <p className="page-prose__muted">
            You are not following anything yet. Open any story and use Follow to be
            told when it gains chapters.
          </p>
        )}

        {works !== null && works.length > 0 && (
          <>
            <p className="page-prose__muted">
              {works.length} work{works.length === 1 ? "" : "s"}
              {unread > 0 && <> · <strong>{unread} with updates</strong></>}
            </p>

            <ul className="follows">
              {works.map(w => (
                <li key={w.id} className={`follows__item ${w.is_new ? "follows__item--new" : ""}`}>
                  <div className="follows__main">
                    <Link href={`/story/${w.id}`} className="follows__title">{w.title}</Link>
                    <p className="follows__meta">
                      {w.author && <>{w.author} · </>}
                      {SITE_LABELS[w.site] ?? w.site}
                      {w.chapter_count != null && <> · {chapterDisplay(w.chapter_count)}</>}
                      {w.word_count != null && <> · {formatWordCount(w.word_count)}</>}
                      {w.updated_at && <> · updated {fmtDate(w.updated_at)}</>}
                    </p>
                  </div>

                  <div className="follows__actions">
                    {/* The count is the whole point of the row, so it is the
                        loudest thing in it and it says how many, not merely
                        that something happened. */}
                    {w.new_chapters > 0 && (
                      <span className="follows__badge">
                        +{w.new_chapters} chapter{w.new_chapters === 1 ? "" : "s"}
                      </span>
                    )}
                    {w.is_new && w.new_chapters === 0 && (
                      <span className="follows__badge follows__badge--soft">updated</span>
                    )}
                    {w.is_new && (
                      <button className="follows__btn" disabled={busy === w.id}
                        onClick={() => markSeen(w.id)}>Mark seen</button>
                    )}
                    <button className="follows__btn follows__btn--quiet" disabled={busy === w.id}
                      onClick={() => unfollow(w.id)}>Unfollow</button>
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}
      </main>
    </div>
  )
}
