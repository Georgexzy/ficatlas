"use client"
import { useEffect, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useAuth } from "@/lib/auth"
import SiteHeader from "../SiteHeader"

interface Session {
  current: boolean
  user_agent: string
  created_at: string | null
  last_used: string | null
  expires_at: string | null
  fp: string
}

function prettyAgent(ua: string): string {
  if (!ua || ua === "Unknown device") return "Unknown device"
  // Lightweight UA prettifier — enough to recognise your own devices.
  const bits: string[] = []
  if (/iPhone/i.test(ua)) bits.push("iPhone")
  else if (/iPad/i.test(ua)) bits.push("iPad")
  else if (/Android/i.test(ua)) bits.push("Android")
  else if (/Macintosh|Mac OS/i.test(ua)) bits.push("Mac")
  else if (/Windows/i.test(ua)) bits.push("Windows")
  else if (/Linux/i.test(ua)) bits.push("Linux")
  if (/Chrome/i.test(ua) && !/Edg/i.test(ua)) bits.push("Chrome")
  else if (/Safari/i.test(ua) && !/Chrome/i.test(ua)) bits.push("Safari")
  else if (/Firefox/i.test(ua)) bits.push("Firefox")
  else if (/Edg/i.test(ua)) bits.push("Edge")
  return bits.length ? bits.join(" · ") : ua.slice(0, 40)
}

function timeAgo(iso: string | null): string {
  if (!iso) return "—"
  const d = new Date(iso).getTime()
  const s = Math.floor((Date.now() - d) / 1000)
  if (s < 60) return "just now"
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

export default function AccountPage() {
  const { user, loading, syncing, lastSyncAt, logout, syncNow, changePassword, deleteAccount } = useAuth()
  const router = useRouter()

  const [sessions, setSessions] = useState<Session[]>([])
  const [curPw, setCurPw] = useState("")
  const [newPw, setNewPw] = useState("")
  const [pwMsg, setPwMsg] = useState<string | null>(null)
  const [pwErr, setPwErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [delPw, setDelPw] = useState("")
  const [delConfirm, setDelConfirm] = useState(false)
  const [delErr, setDelErr] = useState<string | null>(null)

  useEffect(() => {
    if (!loading && !user) router.replace("/login?next=/account")
  }, [loading, user, router])

  const loadSessions = async () => {
    try {
      const r = await fetch("/api/auth/sessions", { credentials: "include" })
      if (r.ok) setSessions((await r.json()).sessions || [])
    } catch {}
  }
  useEffect(() => { if (user) loadSessions() }, [user])

  const onChangePw = async () => {
    setPwMsg(null); setPwErr(null)
    if (newPw.length < 6) { setPwErr("New password must be at least 6 characters"); return }
    setBusy(true)
    try {
      await changePassword(curPw, newPw)
      setPwMsg("Password changed. Other devices were signed out.")
      setCurPw(""); setNewPw(""); loadSessions()
    } catch (e: any) { setPwErr(e.message) }
    finally { setBusy(false) }
  }

  const onLogoutAll = async () => {
    setBusy(true)
    try {
      const fd = new FormData(); fd.append("keep_current", "true")
      await fetch("/api/auth/logout-all", { method: "POST", body: fd, credentials: "include" })
      loadSessions()
    } catch {}
    finally { setBusy(false) }
  }

  const onDelete = async () => {
    setDelErr(null); setBusy(true)
    try {
      await deleteAccount(delPw)
      router.replace("/")
    } catch (e: any) { setDelErr(e.message) }
    finally { setBusy(false) }
  }

  if (loading || !user) return <div className="settings-shell"><p className="loading">Loading…</p></div>

  return (
    <div className="settings-shell">
      <SiteHeader current="account" />
      <h1 className="settings-title">Account</h1>

      {/* Identity + sync */}
      <section className="settings-group">
        <h2 className="settings-group__title">Signed in as</h2>
        <div className="account-identity">
          <span className="account-avatar">{user.username.slice(0, 1).toUpperCase()}</span>
          <div>
            <p className="account-username">{user.username}</p>
            <p className="account-meta">
              Joined {user.created_at ? new Date(user.created_at).toLocaleDateString() : "—"}
              {user.role && <> · <span className={`role-chip role-chip--${user.role}`}>{user.role}</span></>}
            </p>
          </div>
        </div>
        <div className="account-sync-row">
          <span className="account-sync-status">
            {syncing ? "⟳ Syncing…" : lastSyncAt ? `✓ Synced ${timeAgo(new Date(lastSyncAt).toISOString())}` : "Not synced yet"}
          </span>
          <button className="btn btn--ghost" onClick={syncNow} disabled={syncing}>Sync now</button>
        </div>
        <p className="account-help">
          Your bookmarks, reading progress, recent searches and settings sync to this
          account and merge across devices — nothing gets overwritten when you use your
          phone and laptop together.
        </p>
        {/* Say plainly what the role does, rather than leaving someone to
            discover it by finding a button missing. */}
        {user.role === "reader" && (
          <p className="account-help account-help--muted">
            Your account is a <strong>reader</strong>: search, read, bookmark and sync.
            Importing stories and running archive scrapes belong to whoever runs
            this instance.
          </p>
        )}
        {user.role === "admin" && (
          <p className="account-help account-help--muted">
            Your account is an <strong>admin</strong>: you can import stories and run
            archive scrapes. Destructive cleanup and managing accounts stay with
            the owner.
          </p>
        )}
        {user.role === "owner" && (
          <p className="account-help account-help--muted">
            Your account is the <strong>owner</strong>: everything, including cleanup
            batches and setting other people&rsquo;s roles. Scrapes you start leave
            from this machine&rsquo;s IP address.
          </p>
        )}
      </section>

      {/* Active sessions */}
      <section className="settings-group">
        <h2 className="settings-group__title">Devices &amp; sessions</h2>
        {sessions.length === 0 ? (
          <p className="account-help">No other active sessions.</p>
        ) : (
          <ul className="session-list">
            {sessions.map((s, i) => (
              <li key={i} className="session-item">
                <div>
                  <span className="session-device">
                    {prettyAgent(s.user_agent)}
                    {s.current && <span className="session-current">this device</span>}
                  </span>
                  <span className="session-time">Last active {timeAgo(s.last_used)}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
        {sessions.length > 1 && (
          <button className="btn btn--ghost" onClick={onLogoutAll} disabled={busy}>
            Sign out all other devices
          </button>
        )}
      </section>

      {/* Change password */}
      <section className="settings-group">
        <h2 className="settings-group__title">Change password</h2>
        <div className="account-form">
          <input type="password" className="setting-input" placeholder="Current password"
            value={curPw} onChange={e => setCurPw(e.target.value)} autoComplete="current-password" />
          <input type="password" className="setting-input" placeholder="New password (6+ chars)"
            value={newPw} onChange={e => setNewPw(e.target.value)} autoComplete="new-password" />
          <button className="btn btn--primary" onClick={onChangePw} disabled={busy || !curPw || !newPw}>
            {busy ? "Working…" : "Change password"}
          </button>
        </div>
        {pwMsg && <p className="account-success">{pwMsg}</p>}
        {pwErr && <p className="account-error">{pwErr}</p>}
      </section>

      {/* Sign out + danger zone */}
      <section className="settings-group">
        <h2 className="settings-group__title">Session</h2>
        <button className="btn" onClick={async () => { await logout(); router.replace("/") }}>
          Sign out
        </button>
      </section>

      <section className="settings-group settings-group--danger">
        <h2 className="settings-group__title">Delete account</h2>
        <p className="account-help">
          Permanently deletes your account and all synced data. This can&apos;t be undone.
        </p>
        {!delConfirm ? (
          <button className="btn btn--danger" onClick={() => setDelConfirm(true)}>Delete my account…</button>
        ) : (
          <div className="account-form">
            <input type="password" className="setting-input" placeholder="Enter password to confirm"
              value={delPw} onChange={e => setDelPw(e.target.value)} autoComplete="current-password" />
            <div className="account-danger-actions">
              <button className="btn btn--danger" onClick={onDelete} disabled={busy || !delPw}>
                {busy ? "Deleting…" : "Permanently delete"}
              </button>
              <button className="btn btn--ghost" onClick={() => { setDelConfirm(false); setDelPw("") }}>Cancel</button>
            </div>
            {delErr && <p className="account-error">{delErr}</p>}
          </div>
        )}
      </section>
    </div>
  )
}
