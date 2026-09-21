"use client"
export const dynamic = "force-dynamic"
import { useEffect, useState, Suspense } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import Link from "next/link"
import { useAuth } from "@/lib/auth"
import WhyAccount from "../WhyAccount"

function LoginPageInner() {
  const router = useRouter()
  const params = useSearchParams()
  const { user, login, signup, loading } = useAuth()
  const [mode, setMode] = useState<"login" | "signup">("login")
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  // Whether the server has Google credentials at all. Null while unknown, so
  // the button never flashes in and out on load.
  const [sso, setSso] = useState<boolean | null>(null)
  const [ssoError, setSsoError] = useState("")
  const [nextUrl, setNextUrl] = useState("/")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [invite, setInvite] = useState("")
  const [email, setEmail] = useState("")
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

  useEffect(() => {
    fetch("/api/auth/google/status", { credentials: "include" })
      .then(r => r.ok ? r.json() : { configured: false })
      .then(d => setSso(!!d.configured))
      .catch(() => setSso(false))
    // Where to land afterwards, and why it is read here rather than passed:
    // the sign-in page is reached from anywhere, and coming back to the search
    // somebody abandoned is the difference between signing in and starting
    // over.
    try {
      const p = new URLSearchParams(window.location.search)
      const n = p.get("next")
      if (n && n.startsWith("/") && !n.startsWith("//")) setNextUrl(n)
      // Google hands failures back on the query string — see the callback.
      const codes: Record<string, string> = {
        state: "That sign-in attempt expired or did not match. Try again.",
        expired: "That sign-in attempt expired. Try again.",
        exchange: "Google could not complete the sign-in. Try again.",
        access_denied: "Sign-in was cancelled.",
      }
      const sso = p.get("sso")
      if (sso) setSsoError(codes[sso] ?? "Google sign-in did not complete.")
    } catch { /* no query string to read */ }
  }, [])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null); setBusy(true)
    try {
      if (mode === "login") await login(username, password, remember)
      else                  await signup(username, password, invite, remember, email)
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

        {/* Optional, and said so in the label rather than only in the copy.
            Signup never asked for an address, so every account on this site
            began with no way to prove who owns it — and the one account that is
            not the operator's cannot recover its password at all, because there
            is nothing to send a reset to and nothing to check a claim against.
            Not required: an address is a fair thing to ask for and a poor thing
            to demand from someone who came here to search a public index, and a
            required field collects addresses people did not mean to give.
            Honest about delivery, too. Mail from a home connection is routinely
            binned by the big providers, so this does not promise an automatic
            email — it promises that recovery becomes possible at all, which
            without an address it is not. See backend/api/password_reset.py. */}
        {mode === "signup" && (
          <label className="auth-field">
            <span>Email <span className="auth-optional">optional</span></span>
            <input type="email" autoComplete="email" autoCapitalize="off"
              autoCorrect="off" spellCheck={false} placeholder="you@example.com"
              value={email} onChange={e => setEmail(e.target.value)} />
            <p className="auth-hint">
              The only way to get back in if you forget your password. Nothing
              else is ever sent here, and you can add or change it later.
            </p>
          </label>
        )}
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
        {ssoError && <div className="auth-error">{ssoError}</div>}

        <button type="submit" className="auth-submit" disabled={busy}>
          {busy ? "Working…" : (mode === "login" ? "Sign in" : "Create account")}
        </button>

        {/* ASKED FOR, NOT ASSUMED.
            The button appears only when the server says the credentials exist
            — a control that reliably fails is worse than no control, because
            it teaches the reader the site is broken rather than that the
            feature is off. See /api/auth/google/status.

            A plain link, not a fetch: this is a redirect to Google and back,
            and it has to be a real navigation so the browser carries the
            state cookie and lands on the callback itself. */}
        {sso && (
          <>
            <div className="auth-or"><span>or</span></div>
            <a className="auth-google"
               href={`/api/auth/google/start?next=${encodeURIComponent(nextUrl)}`}>
              <svg width="17" height="17" viewBox="0 0 18 18" aria-hidden="true">
                <path fill="#4285F4" d="M17.6 9.2c0-.6-.1-1.3-.2-1.9H9v3.5h4.8a4.1 4.1 0 0 1-1.8 2.7v2.2h2.9c1.7-1.6 2.7-3.9 2.7-6.5z"/>
                <path fill="#34A853" d="M9 18c2.4 0 4.5-.8 6-2.2l-2.9-2.3c-.8.6-1.9.9-3.1.9-2.4 0-4.4-1.6-5.1-3.8H.9v2.3A9 9 0 0 0 9 18z"/>
                <path fill="#FBBC05" d="M3.9 10.6a5.4 5.4 0 0 1 0-3.4V4.9H.9a9 9 0 0 0 0 8.1l3-2.4z"/>
                <path fill="#EA4335" d="M9 3.6c1.3 0 2.5.5 3.4 1.3l2.6-2.6A9 9 0 0 0 .9 4.9l3 2.3C4.6 5.2 6.6 3.6 9 3.6z"/>
              </svg>
              Continue with Google
            </a>
            <small className="auth-note auth-note--sso">
              If an account here already uses that Google address, this signs
              you into it rather than making a second one.
            </small>
          </>
        )}

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
        </p>
      </form>

      {/* The case for bothering, on the screen where it is being weighed.
          This used to be one clause at the foot of the form — "your bookmarks,
          reading progress, recent searches and reader settings sync" — which
          is true, is the smallest of the reasons, and was easy to read past.
          Following WIPs across three archives is the one thing here that no
          archive can do for a reader, and it was not mentioned at all. */}
      <WhyAccount />
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
