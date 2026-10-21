"use client"
export const dynamic = "force-dynamic"
import { useEffect, useState, Suspense } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import Link from "next/link"
import { useAuth } from "@/lib/auth"
import { fetchWithTimeout } from "@/lib/net"
import WhyAccount from "../WhyAccount"
import GoogleIcon from "../GoogleIcon"
import { getLandingBrowse, type LandingBrowse } from "@/lib/api"
import { joinFandomSlugs } from "@/lib/yourFandoms"

function SignupPageInner() {
  const router = useRouter()
  const params = useSearchParams()
  const { user, signup, loading } = useAuth()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const [invite, setInvite] = useState("")
  const [email, setEmail] = useState("")
  const [sso, setSso] = useState<boolean | null>(null)
  const [ssoError, setSsoError] = useState("")
  const [nextUrl, setNextUrl] = useState("/")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [remember, setRemember] = useState(true)
  const [policy, setPolicy] = useState<{ mode: string; needs_code: boolean } | null>(null)
  const [fandoms, setFandoms] = useState<string[]>([])
  const [fandomData, setFandomData] = useState<LandingBrowse | null>(null)
  const next = params.get("next") || "/"

  useEffect(() => {
    if (!loading && user) router.replace(next)
  }, [user, loading, router, next])

  useEffect(() => {
    fetchWithTimeout("/api/auth/signup-policy")
      .then(r => (r.ok ? r.json() : null))
      .then(setPolicy)
      .catch(() => setPolicy(null))
  }, [])

  useEffect(() => {
    getLandingBrowse().then(d => setFandomData(d))
  }, [])

  const signupClosed = policy?.mode === "closed"

  useEffect(() => {
    if (signupClosed) router.replace("/login")
  }, [signupClosed, router])

  useEffect(() => {
    fetchWithTimeout("/api/auth/google/status", { credentials: "include" })
      .then(r => r.ok ? r.json() : { configured: false })
      .then(d => setSso(!!d.configured))
      .catch(() => setSso(false))

    try {
      const p = new URLSearchParams(window.location.search)
      const n = p.get("next")
      if (n && n.startsWith("/") && !n.startsWith("//")) setNextUrl(n)
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
    if (password !== confirm) {
      setError("Passwords do not match")
      setBusy(false)
      return
    }
    // One rule for both directions of this value — see lib/yourFandoms.
    const fandomsStr = joinFandomSlugs(fandoms)
    try {
      await signup(username, password, invite, remember, email, fandomsStr)
      router.replace(next)
    } catch (e: any) {
      setError(e.message || "Signup failed")
    } finally {
      setBusy(false)
    }
  }

  return (
    <main id="main" className="auth-shell">
      <Link href="/" className="auth-logo">Fic<em>Atlas</em></Link>
      <form className="auth-card" onSubmit={submit}>
        <h1 className="visually-hidden">Create a FicAtlas account</h1>

        <label className="auth-field">
          <span>Username</span>
          <input type="text" autoComplete="username" autoCapitalize="off" autoCorrect="off"
            spellCheck={false} required minLength={3} maxLength={30}
            value={username} onChange={e => setUsername(e.target.value)} />
        </label>

        <label className="auth-field">
          <span>Password</span>
          <input type="password" autoComplete="new-password"
            required minLength={6} maxLength={200}
            value={password} onChange={e => setPassword(e.target.value)} />
        </label>

        <label className="auth-field">
          <span>Confirm password</span>
          <input type="password" autoComplete="new-password"
            required minLength={6} maxLength={200}
            value={confirm} onChange={e => setConfirm(e.target.value)} />
        </label>

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

        {/* Optional: pick fandoms. The claim below is the one this control has
            to earn — the picks are read back on the home page
            (/api/follows/welcome → your_fandoms) and edited in Settings, and
            NOTHING else is done with them. It used to promise Following and
            recommendations, neither of which ever read the key. */}
        {fandomData?.fandom && fandomData.fandom.length > 0 && (
          <fieldset className="auth-field auth-field--fandoms">
            <legend>
              <span>Fandoms you read <span className="auth-optional">optional</span></span>
              <p className="auth-hint">
                These come up first on your home page.{" "}
                <Link href="/settings?tab=reading">Change them anytime in Settings</Link>.
              </p>
            </legend>
            <div className="fandom-picker">
              {fandomData.fandom.slice(0, 30).map(f => (
                <label key={f.slug} className="fandom-picker__item">
                  <input
                    type="checkbox"
                    value={f.slug}
                    checked={fandoms.includes(f.slug)}
                    onChange={e => setFandoms(prev =>
                      e.target.checked ? [...prev, f.slug] : prev.filter(s => s !== f.slug)
                    )}
                  />
                  <span className="fandom-picker__name">{f.name}</span>
                  <span className="fandom-picker__count">{f.work_count.toLocaleString()}</span>
                </label>
              ))}
            </div>
            <p className="auth-hint">
              <a href="/fandoms" target="_blank" rel="noopener noreferrer">
                All {fandomData.fandom.length} fandoms →
              </a>
            </p>
          </fieldset>
        )}

        {policy?.needs_code && (
          <label className="auth-field">
            <span>Invite code</span>
            <input type="text" autoCapitalize="off" autoCorrect="off" spellCheck={false}
              required value={invite} onChange={e => setInvite(e.target.value)} />
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
          {busy ? "Working…" : "Create account"}
        </button>

        {sso && (
          <>
            <div className="auth-or"><span>or</span></div>
            <a className="auth-google"
               href={`/api/auth/google/start?next=${encodeURIComponent(nextUrl)}`}>
              <GoogleIcon />
              Sign up with Google
            </a>
            <small className="auth-note auth-note--sso">
              If an account here already uses that Google address, this signs
              you into it rather than making a second one.
            </small>
          </>
        )}

        <p className="auth-hint">
          Your username can be anything 3–30 chars. No email needed.
        </p>
      </form>

      <WhyAccount />
    </main>
  )
}

export default function SignupPage() {
  return (
    <Suspense fallback={null}>
      <SignupPageInner />
    </Suspense>
  )
}