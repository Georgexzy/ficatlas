"use client"

import { useEffect, useState, useCallback } from "react"
import Link from "next/link"
import { useAuth } from "@/lib/auth"
import { formatWordCount, chapterDisplay, displayTitle, SITE_LABELS } from "@/lib/api"
import { fetchWithTimeout, USER_TIMEOUT_MS } from "@/lib/net"

// The other half of following a work. Rendered as the first tab of /library.
//
// It was its own page at /follows, which put five lists of the reader's own works
// across two destinations — Following on one, and Bookmarks, Reading, Offline and
// their imports on the other — with nothing on either saying the other existed.
// They answer one question ("what have I got?"), so they are one page, and
// Following leads because it is the only one of them that CHANGES on its own.
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

interface MailPrefs {
  enabled: boolean
  frequency: "daily" | "hourly"
  has_email: boolean
  email: string | null
  last_sent_at: string | null
  following: number
  mail_available: boolean
}

const FREQUENCIES: { value: MailPrefs["frequency"]; title: string; body: string }[] = [
  { value: "daily", title: "Daily digest",
    body: "At most one email a day, listing everything that updated." },
  { value: "hourly", title: "As it happens",
    body: "Within the hour of a new chapter, several updates batched into one email." },
]

function ago(iso: string | null): string | null {
  if (!iso) return null
  const ms = Date.now() - new Date(iso).getTime()
  if (!Number.isFinite(ms)) return null
  const h = Math.round(ms / 3_600_000)
  if (h < 1) return "less than an hour ago"
  if (h < 24) return `${h} hour${h === 1 ? "" : "s"} ago`
  const d = Math.round(h / 24)
  return `${d} day${d === 1 ? "" : "s"} ago`
}

// Opt-in mail when a followed work gains chapters (backend/follow_email.py).
// The list below only helps a reader who comes back; this is what brings them
// back. Off until switched on, one digest rather than a mail per chapter, and
// each chapter announced once.
//
// A settings card rather than a sentence with a checkbox: it says where the
// mail goes, what each frequency means before you pick it, when the last one
// went, and lets you send a test — "is this working?" is the question every
// opt-in mail gets, usually from somebody whose follows have not updated yet.
function FollowMail() {
  const [prefs, setPrefs] = useState<MailPrefs | null>(null)
  const [msg, setMsg] = useState<{ text: string; tone: "ok" | "err" } | null>(null)
  const [busy, setBusy] = useState(false)
  const [testing, setTesting] = useState(false)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    fetchWithTimeout("/api/follows/email", { credentials: "include" }, USER_TIMEOUT_MS)
      .then(r => (r.ok ? r.json() : null)).then(setPrefs).catch(() => {})
  }, [])

  // The server is not offering mail at all (no relay configured), so a switch
  // would promise something nothing will deliver.
  if (!prefs || !prefs.mail_available) return null

  const save = async (next: { enabled: boolean, frequency: MailPrefs["frequency"] }) => {
    setBusy(true); setMsg(null)
    try {
      const r = await fetchWithTimeout("/api/follows/email", {
        method: "PUT", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(next),
      }, USER_TIMEOUT_MS)
      const d = await r.json().catch(() => null)
      if (!r.ok) { setMsg({ text: d?.detail || "Could not save that.", tone: "err" }); return }
      const turnedOn = d.enabled && !prefs.enabled
      setPrefs(d)
      setMsg(turnedOn
        ? { text: "On. You will hear about chapters posted from now on.", tone: "ok" }
        : !d.enabled ? { text: "Update emails are off.", tone: "ok" }
        : { text: "Saved.", tone: "ok" })
    } catch {
      setMsg({ text: "Could not save that.", tone: "err" })
    } finally {
      setBusy(false)
    }
  }

  const sendTest = async () => {
    setTesting(true); setMsg(null)
    try {
      const r = await fetchWithTimeout("/api/follows/email/test",
        { method: "POST", credentials: "include" }, USER_TIMEOUT_MS)
      const d = await r.json().catch(() => null)
      setMsg(r.ok
        ? { text: `Test email sent to ${d?.to ?? "your address"}. If it is not there in a minute, check spam.`, tone: "ok" }
        : { text: d?.detail || "Could not send a test email.", tone: "err" })
    } catch {
      setMsg({ text: "Could not send a test email.", tone: "err" })
    } finally {
      setTesting(false)
    }
  }

  const noAddress = !prefs.has_email
  const lastSent = ago(prefs.last_sent_at)

  return (
    // Collapsed to one line by default: the card is a setting you change once,
    // and opened it pushed the list of followed stories — the reason for this
    // tab — most of a phone screen down. The line still says the state.
    <details className={`mail-card${prefs.enabled ? " mail-card--on" : ""}`}
      open={open} onToggle={e => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary className="mail-card__summary">
        <span className="mail-card__summary-title">Update emails</span>
        <span className={`mail-card__pill${prefs.enabled ? " mail-card__pill--on" : ""}`}>
          {prefs.enabled
            ? (prefs.frequency === "daily" ? "On · daily digest" : "On · as it happens")
            : "Off"}
        </span>
        <span className="mail-card__chev" aria-hidden="true" />
      </summary>
      <div className="mail-card__body">
      <div className="mail-card__head">
        <div className="mail-card__intro">
          <p id="mail-card-title" className="mail-card__lede">
            One email when stories you follow get new chapters — across AO3,
            FanFiction.net and FictionAlley.
          </p>
        </div>
        <button type="button" role="switch" aria-checked={prefs.enabled}
          aria-label="Update emails"
          className={`switch${prefs.enabled ? " switch--on" : ""}`}
          disabled={busy || (noAddress && !prefs.enabled)}
          onClick={() => save({ enabled: !prefs.enabled, frequency: prefs.frequency })}>
          <span className="switch__thumb" aria-hidden="true" />
          <span className="visually-hidden">{prefs.enabled ? "On" : "Off"}</span>
        </button>
      </div>

      {noAddress ? (
        <p className="mail-card__note">
          Your account has no email address yet.{" "}
          <Link href="/settings?tab=account">Add one</Link> to turn this on — it is
          used for this and password resets, and nothing else.
        </p>
      ) : (
        <p className="mail-card__to">
          Sent to <strong>{prefs.email}</strong>
          {" · "}<Link href="/settings?tab=account">change</Link>
        </p>
      )}

      {prefs.enabled && (
        <>
          <fieldset className="mail-card__freq" disabled={busy}>
            <legend className="mail-card__legend">How often</legend>
            {FREQUENCIES.map(f => (
              <label key={f.value}
                className={`freq-option${prefs.frequency === f.value ? " freq-option--on" : ""}`}>
                <input type="radio" name="mail-frequency" value={f.value}
                  checked={prefs.frequency === f.value}
                  onChange={() => save({ enabled: true, frequency: f.value })} />
                <span className="freq-option__title">{f.title}</span>
                <span className="freq-option__body">{f.body}</span>
              </label>
            ))}
          </fieldset>

          <div className="mail-card__foot">
            <span className="mail-card__status">
              {lastSent
                ? <>Last email {lastSent}</>
                : <>No email yet — the first goes out when a followed story updates</>}
              {" · "}following {prefs.following.toLocaleString()}{" "}
              {prefs.following === 1 ? "story" : "stories"}
            </span>
            <button type="button" className="btn btn--ghost mail-card__test"
              onClick={sendTest} disabled={testing}>
              {testing ? "Sending…" : "Send me a test email"}
            </button>
          </div>
        </>
      )}

      {msg && (
        <p className={`mail-card__msg mail-card__msg--${msg.tone}`} role="status">{msg.text}</p>
      )}
      </div>
    </details>
  )
}

export default function FollowingTab() {
  const { user, loading: authLoading } = useAuth()
  const [works, setWorks] = useState<FollowedWork[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await fetchWithTimeout("/api/follows",
        { credentials: "include" }, USER_TIMEOUT_MS)
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
      await fetchWithTimeout(`/api/follows/${id}/seen`,
        { method: "POST", credentials: "include" }, USER_TIMEOUT_MS)
      setWorks(w => (w ?? []).map(x =>
        x.id === id ? { ...x, new_chapters: 0, is_new: false } : x))
    } finally {
      setBusy(null)
    }
  }

  const unfollow = async (id: string) => {
    setBusy(id)
    try {
      await fetchWithTimeout(`/api/follows/${id}`,
        { method: "DELETE", credentials: "include" }, USER_TIMEOUT_MS)
      setWorks(w => (w ?? []).filter(x => x.id !== id))
    } finally {
      setBusy(null)
    }
  }

  const unread = (works ?? []).filter(w => w.is_new).length

  return (
    <>
        {!authLoading && !user && (
          <p className="page-prose__muted">
            <Link href="/login?next=/library%3Ftab%3Dfollowing">Sign in</Link> to
            follow works and be told when they update. Following is the one thing
            no single archive can do for you — a WIP on AO3, a long-runner on
            FanFiction.net and a finished series on FictionAlley in one list,
            and, if you want it, one email when any of them gains chapters.
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
            <FollowMail />
            <p className="page-prose__muted">
              {works.length} work{works.length === 1 ? "" : "s"}
              {unread > 0 && <> · <strong>{unread} with updates</strong></>}
            </p>

            <ul className="follows">
              {works.map(w => (
                <li key={w.id} className={`follows__item ${w.is_new ? "follows__item--new" : ""}`}>
                  <div className="follows__main">
                    <Link href={`/story/${w.id}`} className="follows__title">{displayTitle(w.title)}</Link>
                    <p className="follows__meta">
                      {w.author && <>{w.author} · </>}
                      {SITE_LABELS[w.site] ?? w.site}
                      {w.chapter_count != null && <> · {chapterDisplay(w.chapter_count, null, w.status)}</>}
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
    </>
  )
}
