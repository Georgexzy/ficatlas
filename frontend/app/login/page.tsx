"use client"
export const dynamic = "force-dynamic"
import { useState, useEffect, Suspense } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import Link from "next/link"
import { useAuth } from "@/lib/auth"

function LoginPageInner() {
  const router = useRouter()
  const params = useSearchParams()
  const { user, login, signup, loading } = useAuth()
  const [mode, setMode] = useState<"login" | "signup">("login")
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [invite, setInvite] = useState("")
  // Default on, which is what the site already did for everyone — the box exists
  // so a shared or borrowed device can opt OUT, not to make people opt in to
  // something that already worked.
  const [remember, setRemember] = useState(true)
  // What the server will accept, asked rather than assumed. The endpoint exists
  // precisely so this page does not have to guess, and it exposes no secret —
  // only whether a code is needed, which the failing 403 announced anyway.
  const [policy, setPolicy] = useState<{ mode: string; needs_code: boolean } | null>(null)
  const next = params.get("next") || "/"

  useEffect(() => {
    if (!loading && user) router.replace(next)
  }, [user, loading, router, next])

  useEffect(() => {
    fetch("/api/auth/signup-policy")
      .then(r => (r.ok ? r.json() : null))
      .then(setPolicy)
      // A failed policy fetch must not remove the signup tab: falling back to
      // "open" leaves the form working exactly as it did, and the server is the
      // thing that actually enforces the mode.
      .catch(() => setPolicy(null))
  }, [])

  const signupClosed = policy?.mode === "closed"

  // Never leave the user on a tab that cannot work. If the site is closed to new
  // accounts, the tab is not shown at all, so nobody fills in a form to be told
  // no at the end of it.
  useEffect(() => {
    if (signupClosed && mode === "signup") setMode("login")
  }, [signupClosed, mode])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null); setBusy(true)
    try {
      if (mode === "login") await login(username, password, remember)
      else                  await signup(username, password, invite, remember)
      router.replace(next)
    } catch (e: any) {
      setError(e.message || `${mode} failed`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-shell">
      <Link href="/" className="auth-logo">Fic<em>Atlas</em></Link>
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-tabs">
          <button type="button" className={`auth-tab ${mode === "login" ? "auth-tab--on" : ""}`}
            onClick={() => setMode("login")}>Sign in</button>
          {!signupClosed && (
            <button type="button" className={`auth-tab ${mode === "signup" ? "auth-tab--on" : ""}`}
              onClick={() => setMode("signup")}>Create account</button>
          )}
        </div>

        <label className="auth-field">
          <span>Username</span>
          <input type="text" autoComplete="username" autoCapitalize="off" autoCorrect="off"
            spellCheck={false} required minLength={3} maxLength={30}
            value={username} onChange={e => setUsername(e.target.value)} />
        </label>

        <label className="auth-field">
          <span>Password</span>
          <input type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            required minLength={6} maxLength={200}
            value={password} onChange={e => setPassword(e.target.value)} />
        </label>

        {mode === "signup" && policy?.needs_code && (
          <label className="auth-field">
            <span>Invite code</span>
            <input type="text" autoCapitalize="off" autoCorrect="off" spellCheck={false}
              required value={invite} onChange={e => setInvite(e.target.value)} />
            {/* Says who to ask rather than only that a code is required — the
                latter is a dead end for someone who does not know the site is
                invite-only, which is everyone arriving at it for the first
                time. */}
            <small className="auth-note">
              FicAtlas is invite-only for now. Ask whoever sent you here for the code.
            </small>
          </label>
        )}

        <label className="auth-remember">
          <input type="checkbox" checked={remember}
            onChange={e => setRemember(e.target.checked)} />
          <span>
            Stay signed in on this device
            <small>Leave this off on a shared or public computer — you will be
              signed out when the browser closes.</small>
          </span>
        </label>

        {error && <div className="auth-error">{error}</div>}

        <button type="submit" className="auth-submit" disabled={busy}>
          {busy ? "Working…" : (mode === "login" ? "Sign in" : "Create account")}
        </button>

        {mode === "login" && (
          <p className="auth-hint auth-hint--forgot">
            <a href="/forgot">Forgotten your password?</a>
          </p>
        )}

        <p className="auth-hint">
          {mode === "login"
            ? (remember
                ? "Stays signed in for 90 days on this device."
                : "You will be signed out when this browser closes.")
            : "Your username can be anything 3–30 chars. No email needed."}
          {" "}Your bookmarks, reading progress, recent searches and reader
          settings sync automatically and merge across devices.
        </p>
      </form>
    </div>
  )
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginPageInner />
    </Suspense>
  )
}
