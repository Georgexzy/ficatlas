"use client"

import Link from "next/link"
import BackLink from "../BackLink"
import { useCallback, useEffect, useState } from "react"
import SiteHeader from "../SiteHeader"
import { useAuth } from "@/lib/auth"

// The other half of the takedown flow.
//
// Submitting one has worked for a while: the text comes down immediately, the
// row is written, an admin is notionally informed. Nothing ever showed it to
// them. Requests went into a table with no page, so "we will email you when
// someone has looked at it" — which the form promises in those words — was a
// promise with no mechanism behind it.
//
// Auto-hiding means nothing here is urgent; the text is already down before
// anyone opens this. What is outstanding is the DECISION: whether the removal
// stands, and telling the person who asked. That is what this page is for, and
// it is why the queue defaults to pending rather than to everything.
interface Takedown {
  id: string
  story_url: string
  claimant: string
  email: string
  relationship: string
  detail: string | null
  state: string
  created_at: string
  story_title: string | null
  story_id: string | null
  text_hidden: boolean
  delisted: boolean
  is_hosted: boolean
  source_url: string | null
}

const RELATIONSHIP_LABEL: Record<string, string> = {
  author: "the author",
  agent: "acting for the author",
  other: "someone else",
}

export default function TakedownsPage() {
  const { user, loading: authLoading } = useAuth()
  const isAdmin = !!user?.can_manage

  const [items, setItems] = useState<Takedown[]>([])
  const [state, setState] = useState<"pending" | "all">("pending")
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notes, setNotes] = useState<Record<string, string>>({})

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const r = await fetch(`/api/takedown?state=${state}&limit=200`, { credentials: "include" })
      if (!r.ok) throw new Error(r.status === 401 || r.status === 403
        ? "You are not signed in as someone who can review these."
        : `Could not load the queue (${r.status}).`)
      setItems(await r.json())
    } catch (e: any) {
      setError(e.message); setItems([])
    } finally { setLoading(false) }
  }, [state])

  useEffect(() => { if (isAdmin) load() }, [isAdmin, load])

  // uphold=false restores BOTH the text and the listing, so "reject" means what
  // it says. delist only applies on uphold — it is the stronger action, and it
  // is never taken by rejecting something.
  const resolve = async (id: string, uphold: boolean, delist = false) => {
    setBusy(id); setError(null)
    try {
      const fd = new FormData()
      fd.append("uphold", String(uphold))
      fd.append("delist", String(delist))
      if (notes[id]) fd.append("note", notes[id])
      const r = await fetch(`/api/takedown/${id}/resolve`,
        { method: "POST", body: fd, credentials: "include" })
      if (!r.ok) throw new Error(`That did not save (${r.status}).`)
      await load()
    } catch (e: any) {
      setError(e.message)
    } finally { setBusy(null) }
  }

  if (authLoading) return (
    <div className="settings-shell"><SiteHeader />
      <BackLink fallback="/settings" fallbackLabel="Settings" /><p className="loading">Loading…</p></div>
  )

  // Not an operator: no queue, no hint that one exists. Same reasoning as the
  // rest of the admin surface — showing a control that 403s teaches a reader
  // the site is broken rather than that this is not theirs.
  if (!isAdmin) return (
    <div className="page-prose">
      <SiteHeader />
      <h1>Not found</h1>
      <p>There is nothing here for this account.</p>
      <p><Link href="/" className="card-btn card-btn--primary">Back to search</Link></p>
    </div>
  )

  return (
    <div className="settings-shell">
      <SiteHeader />
      <BackLink fallback="/settings" fallbackLabel="Settings" />
      <h1 className="settings-title">Takedown requests</h1>
      <p className="settings-lede">
        Text comes down automatically when a request arrives, so nothing here is
        waiting on you to protect anyone. What is outstanding is the decision —
        and telling the person who asked, which the form promised them.
      </p>

      <div className="takedown-queue__filters">
        {(["pending", "all"] as const).map(s => (
          <button key={s} onClick={() => setState(s)}
            className={`pill ${state === s ? "pill--on" : ""}`}>
            {s === "pending" ? "Waiting" : "Everything"}
          </button>
        ))}
        <button className="btn btn--ghost takedown-queue__refresh"
          onClick={load} disabled={loading}>
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>

      {error && <p className="settings-save-error" role="alert">{error}</p>}

      {!loading && items.length === 0 && (
        <div className="library-empty">
          <p>{state === "pending" ? "Nothing waiting." : "No requests have ever been made."}</p>
          <p className="library-empty__hint">
            Authors reach this through <Link href="/takedown">the takedown form</Link>,
            linked from About and the footer of every page.
          </p>
        </div>
      )}

      {items.map(t => (
        <section key={t.id} className={`takedown-card takedown-card--${t.state}`}>
          <header className="takedown-card__head">
            <div>
              <h2 className="takedown-card__title">
                {t.story_title ?? <em>No story matched this address</em>}
              </h2>
              <p className="takedown-card__meta">
                <strong>{t.claimant}</strong> ({RELATIONSHIP_LABEL[t.relationship] ?? t.relationship})
                {" · "}
                <a href={`mailto:${t.email}`}>{t.email}</a>
                {" · "}
                {new Date(t.created_at).toLocaleString()}
              </p>
            </div>
            <span className={`badge takedown-card__state takedown-card__state--${t.state}`}>
              {t.state}
            </span>
          </header>

          {/* Current state of the WORK, not of the request. These drift apart:
              a second request, or an admin acting directly, changes the story
              without changing the row that asked. */}
          <ul className="takedown-card__flags">
            <li className={t.text_hidden ? "is-on" : ""}>
              {t.text_hidden ? "✓ Text hidden" : "Text readable"}
            </li>
            <li className={t.delisted ? "is-on" : ""}>
              {t.delisted ? "✓ Delisted from search" : "Still listed"}
            </li>
            {!t.is_hosted && !t.text_hidden && (
              <li title="We only ever held metadata for this one — there was no text to take down.">
                Listing only
              </li>
            )}
          </ul>

          <p className="takedown-card__url">
            They gave: <code>{t.story_url}</code>
            {t.source_url && t.source_url !== t.story_url && (
              <> · we matched: <code>{t.source_url}</code></>
            )}
            {t.story_id && (
              <> · <Link href={`/story/${t.story_id}`}>open the story</Link></>
            )}
          </p>

          {t.detail && <blockquote className="takedown-card__detail">{t.detail}</blockquote>}

          {t.state === "pending" ? (
            <div className="takedown-card__actions">
              <input className="setting-input takedown-card__note"
                placeholder="Note for the record (optional)"
                value={notes[t.id] ?? ""}
                onChange={e => setNotes(n => ({ ...n, [t.id]: e.target.value }))} />
              <div className="takedown-card__buttons">
                <button className="btn btn--primary" disabled={busy === t.id}
                  onClick={() => resolve(t.id, true)}
                  title="The removal stands. Text stays down; the listing is unchanged.">
                  Uphold
                </button>
                {!t.delisted && (
                  <button className="btn" disabled={busy === t.id}
                    onClick={() => resolve(t.id, true, true)}
                    title="Uphold and also remove the listing from search entirely.">
                    Uphold &amp; delist
                  </button>
                )}
                <button className="btn btn--ghost" disabled={busy === t.id}
                  onClick={() => resolve(t.id, false)}
                  title="Not a valid request. Restores both the text and the listing.">
                  Reject &amp; restore
                </button>
              </div>
            </div>
          ) : (
            <p className="takedown-card__resolved">
              {t.state === "upheld" ? "Upheld" : "Rejected"} — remember to reply to{" "}
              <a href={`mailto:${t.email}`}>{t.email}</a> if you have not.
            </p>
          )}
        </section>
      ))}
    </div>
  )
}
