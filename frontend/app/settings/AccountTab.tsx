"use client"
import { useEffect, useState, useCallback } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useAuth } from "@/lib/auth"
import { fetchJson } from "@/lib/errors"

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

const ROLE_OPTIONS = ["reader", "admin", "owner"] as const

// Owner-only account management: who exists, and what role each account has.
// The endpoints behind this (GET /api/auth/users, POST /api/auth/users/{id}/role)
// are guarded owner-only server-side, so this UI is a thin wrapper over powers
// the backend already enforces.
function ManageAccounts({ selfId }: { selfId: string }) {
  const [users, setUsers] = useState<{ id: string; username: string; role: string; last_login: string | null }[] | null>(null)
  const [msg, setMsg] = useState("")
  const [err, setErr] = useState("")

  // Only an HTTP error was handled. A network throw — backend down, restarting,
  // unreachable — rejected out of the effect that calls this as an unhandled
  // rejection, so `users` stayed null forever behind its loading state with
  // nothing on screen to say why. That is the failure mode most likely to occur
  // and the only one that said nothing.
  const load = useCallback(async () => {
    try {
      const d = await fetchJson("/api/auth/users", { credentials: "include" })
      if (d?.users) { setUsers(d.users); setErr("") }
    } catch (e: any) {
      setErr(e?.message || "Could not load accounts.")
    }
  }, [])

  useEffect(() => { load() }, [load])

  const setRole = async (id: string, role: string) => {
    setMsg(""); setErr("")
    const fd = new FormData(); fd.append("role", role)
    const r = await fetch(`/api/auth/users/${id}/role`, { method: "POST", body: fd, credentials: "include" })
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      setErr(d.detail || "Could not change that account's role.")
      return
    }
    setMsg("Role updated.")
    load()
  }

  return (
    <section className="settings-group">
      <h2 className="settings-group__title">Manage accounts</h2>
      <p className="account-help">
        Who has access to this instance and what each account may do. Only you,
        the owner, can change roles.
      </p>
      {msg && <p className="settings-save-ok">{msg}</p>}
      {err && <p className="settings-save-error">{err}</p>}
      <div className="manage-users">
        {users === null ? <p className="account-help account-help--muted">Loading…</p> : (
          users.map(u => (
            <div key={u.id} className="manage-user">
              <div className="manage-user__id">
                <span className="manage-user__name">{u.username}</span>
                <span className="manage-user__meta">Last active {u.last_login ? timeAgo(u.last_login) : "never"}</span>
              </div>
              <select
                className="manage-user__select"
                value={u.role}
                disabled={u.id === selfId}
                title={u.id === selfId ? "You cannot change your own role here." : undefined}
                onChange={e => setRole(u.id, e.target.value)}
              >
                {ROLE_OPTIONS.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
          ))
        )}
      </div>
    </section>
  )
}

export default function AccountTab() {
  const { user, loading, syncing, lastSyncAt, logout, syncNow, changePassword, deleteAccount } = useAuth()
  const router = useRouter()

  // Whether a password EXISTS. The server computes it (see /me) so the UI never
  // has to guess, and every control that would demand one branches on it: an
  // account made with Google has none, and a form asking for it is not merely
  // awkward there, it is impossible to satisfy.
  const hasPassword = user?.has_password !== false

  const [sessions, setSessions] = useState<Session[]>([])
  const [email, setEmail] = useState("")
  const [emailPw, setEmailPw] = useState("")
  const [emailMsg, setEmailMsg] = useState<string | null>(null)
  const [emailBusy, setEmailBusy] = useState(false)

  // Changing the address requires the current password: without that, anyone
  // holding a stolen session could repoint it at themselves and then use the
  // reset flow to take the account outright.
  const saveEmail = async (e: React.FormEvent) => {
    e.preventDefault(); setEmailBusy(true); setEmailMsg(null)
    try {
      const fd = new FormData()
      // One of the two, depending on whether this account HAS a password. A
      // Google-only account has none, so the password gate made this form
      // impossible to satisfy — see confirm_identity in backend/api/auth.py.
      fd.append("password", emailPw); fd.append("email", email)
      fd.append("confirm", emailPw)
      const r = await fetch("/api/auth/email", { method: "POST", body: fd, credentials: "include" })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "Could not save that.")
      setEmailMsg(d.email ? `Saved — resets will go to ${d.email}.` : "Address removed.")
      setEmailPw("")
    } catch (err: any) { setEmailMsg(err.message) }
    finally { setEmailBusy(false) }
  }
  const [curPw, setCurPw] = useState("")
  const [newPw, setNewPw] = useState("")
  const [pwMsg, setPwMsg] = useState<string | null>(null)
  const [pwErr, setPwErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [delPw, setDelPw] = useState("")
  const [delName, setDelName] = useState("")
  const [delConfirm, setDelConfirm] = useState(false)
  const [delErr, setDelErr] = useState<string | null>(null)

  const loadSessions = async () => {
    try {
      setSessions((await fetchJson("/api/auth/sessions", { credentials: "include" })).sessions || [])
    } catch {}
  }
  useEffect(() => { if (user) loadSessions() }, [user])
  useEffect(() => { setEmail((user as any)?.email ?? "") }, [user])

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
      await deleteAccount(delPw, delName)
      router.replace("/")
    } catch (e: any) { setDelErr(e.message) }
    finally { setBusy(false) }
  }

  if (loading) return <p className="loading">Loading…</p>

  // Signed out, this tab has nothing of its own to show. It used to be a page
  // that bounced you to /login, which lost whatever you had come to Settings to
  // do; as one tab among several the rest of the page still works, so it makes
  // its case and leaves the choice alone.
  if (!user) return (
    <section className="settings-group">
      <h2 className="settings-group__title">You are not signed in</h2>
      <p className="account-help">
        Everything on the other tabs works without an account and stays on this
        device. An account adds the things a device cannot do on its own: one
        list of followed works across all three archives, and a shelf that
        survives a cleared browser or a new phone.
      </p>
      <p>
        <Link href="/login?next=/settings%3Ftab%3Daccount"
          className="card-btn card-btn--primary">Sign in or make an account</Link>
      </p>
    </section>
  )

  return (
    <>

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

      {/* Role preview — owners/admins only */}
      {user?.can_import && !user?.previewing && (
        <section className="settings-group">
          <h2 className="settings-group__title">See the site as a reader</h2>
          <p className="account-help">
            Loads the site with your own permissions reduced, so you can check
            what an ordinary reader sees. It does not sign you in as anybody
            else and touches no other account — only what this session may do
            changes. Leave it from the banner at any time.
          </p>
          <div className="account-danger-actions">
            <button className="card-btn" onClick={async () => {
              const fd = new FormData(); fd.append("role", "reader")
              await fetch("/api/auth/view-as", { method: "POST", body: fd, credentials: "include" })
              window.location.href = "/"
            }}>Preview as reader</button>
          </div>
        </section>
      )}

      {/* Owner-only: list accounts and change roles. */}
      {user?.can_manage && !user?.previewing && <ManageAccounts selfId={user.id} />}

      {/* HOW YOU SIGN IN — one place, because it is one subject.

          These four were spread down the page in the order they were built: the
          email address, then Devices & sessions, then Google, then the password
          controls. So "how do I get into this account" was answered in four
          places, with an unrelated section wedged through the middle of it, and
          anyone changing their sign-in had to scroll past a list of devices to
          find the other half of it.

          Ordered by how a person reaches for them: the password, then the Google
          link, then the address — which is not a way IN at all but the way BACK
          in, and reads correctly as the fallback it is.

          Devices & sessions moves below. It answers "who is signed in right
          now", a different question from "how do I sign in", and it was the
          thing splitting this group in two. */}
      <div className="account-signin">
        <h2 className="settings-group__title">How you sign in</h2>
      {/* SET a password, for an account that has never had one. Only shown when
          there is none: an account created by Google sign-in cannot use Change
          password below, because that verifies a current password it does not
          have. Without this the reader was locked into Google for ever. */}
      {user && !user.has_password && (
        <div className="account-signin__part">
          <h3 className="account-signin__sub">Add a password</h3>
          <p className="account-help">
            This account signs in with Google and has no password. Adding one
            gives you a second way in, so losing access to Google does not lose
            you the account.
          </p>
          <div className="account-form">
            <input type="password" className="setting-input"
              placeholder="New password (6+ chars)" value={newPw}
              onChange={e => setNewPw(e.target.value)} autoComplete="new-password" />
            <button className="btn btn--primary" disabled={busy || newPw.length < 6}
              onClick={async () => {
                setBusy(true); setPwErr(""); setPwMsg("")
                try {
                  const body = new URLSearchParams({ new_password: newPw })
                  const r = await fetch("/api/auth/set-password",
                    { method: "POST", credentials: "include", body })
                  const d = await r.json().catch(() => ({}))
                  if (!r.ok) throw new Error(d.detail || "Could not set password")
                  setPwMsg(d.message || "Password set.")
                  setNewPw("")
                } catch (e: any) {
                  setPwErr(e.message || "Could not set password")
                } finally { setBusy(false) }
              }}>
              {busy ? "Working…" : "Set password"}
            </button>
          </div>
          {pwMsg && <p className="account-success">{pwMsg}</p>}
          {pwErr && <p className="account-error">{pwErr}</p>}
        </div>
      )}
      {/* Change password — only where there is one to change. */}
      {user?.has_password && (
      <div className="account-signin__part">
        <h3 className="account-signin__sub">Change password</h3>
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
      </div>
      )}
      {/* GOOGLE, for linking an existing account rather than only signing in.
          Linking from settings is the safer direction: the reader is already
          authenticated here, so there is no question about which account the
          Google identity should join. At the login page it has to be inferred
          from a verified email address, which is why that path insists on
          Google reporting the address verified. */}
      <div className="account-signin__part">
        <h3 className="account-signin__sub">Google sign-in</h3>
        {user?.google_linked ? (
          <>
            <p className="account-help">
              Linked. You can sign in with Google on any device.
              {!user?.has_password && (
                <> Set a password below first if you want to unlink — otherwise
                   there would be no way back in.</>
              )}
            </p>
            <button className="btn btn--ghost"
              disabled={busy || !user?.has_password}
              onClick={async () => {
                setBusy(true)
                try {
                  await fetch("/api/auth/google/unlink",
                              { method: "POST", credentials: "include" })
                  location.reload()
                } finally { setBusy(false) }
              }}>
              Unlink Google
            </button>
          </>
        ) : (
          <>
            <p className="account-help">
              Link your Google account and you can sign in with one tap, without
              a password.
            </p>
            <a className="btn btn--primary"
               href="/api/auth/google/start?link=1">Link Google account</a>
          </>
        )}
      </div>
      {/* Contact address — the only route back into a locked-out account */}
      <div className="account-signin__part">
        <h3 className="account-signin__sub">Email address</h3>
        <p className="account-help">
          {user?.email
            ? <>Password resets will be sent to <strong>{user.email}</strong>.</>
            : <>Optional. Without one, a forgotten password can only be reset by
               contacting the site directly for a code.</>}
        </p>
        <form className="account-form" onSubmit={saveEmail}>
          <input type="email" placeholder="you@example.com" value={email}
            onChange={e => setEmail(e.target.value)} autoComplete="email" />
          {/* A Google-only account has no password to type, and asking for one
              made this form unsatisfiable rather than merely awkward. It asks
              for the username instead — the one thing only the account holder
              knows they are — which is the same confirmation the delete button
              below uses. */}
          {hasPassword
            ? <input type="password" placeholder="Your current password" value={emailPw}
                onChange={e => setEmailPw(e.target.value)} autoComplete="current-password" />
            : <input type="text" placeholder={`Type "${user.username}" to confirm`}
                value={emailPw} onChange={e => setEmailPw(e.target.value)}
                autoComplete="off" />}
          <button type="submit" className="card-btn" disabled={emailBusy}>
            {emailBusy ? "Saving…" : user?.email ? "Update" : "Add address"}
          </button>
        </form>
        {emailMsg && <p className="account-help account-help--muted">{emailMsg}</p>}
      </div>
      </div>

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
            {/* THE BUG THIS FIXES: the button was `disabled={!delPw}` behind a
                password field, and an account made with Google sign-in has no
                password — so for those readers the button could never become
                enabled and the account could not be deleted at all, while
                /privacy promises in as many words that it can. A promise the
                code cannot keep is worse than a missing feature, and this is the
                one it is least acceptable to break. */}
            {hasPassword
              ? <input type="password" className="setting-input" placeholder="Enter password to confirm"
                  value={delPw} onChange={e => setDelPw(e.target.value)} autoComplete="current-password" />
              : <input type="text" className="setting-input" autoComplete="off"
                  placeholder={`Type "${user.username}" to confirm`}
                  value={delName} onChange={e => setDelName(e.target.value)} />}
            <div className="account-danger-actions">
              <button className="btn btn--danger" onClick={onDelete}
                disabled={busy || (hasPassword ? !delPw : !delName.trim())}>
                {busy ? "Deleting…" : "Permanently delete"}
              </button>
              <button className="btn btn--ghost"
                onClick={() => { setDelConfirm(false); setDelPw(""); setDelName("") }}>Cancel</button>
            </div>
            {delErr && <p className="account-error">{delErr}</p>}
          </div>
        )}
      </section>
    </>
  )
}
