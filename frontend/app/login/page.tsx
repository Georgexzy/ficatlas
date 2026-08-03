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
  const next = params.get("next") || "/"

  useEffect(() => {
    if (!loading && user) router.replace(next)
  }, [user, loading, router, next])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null); setBusy(true)
    try {
      if (mode === "login") await login(username, password)
      else                  await signup(username, password)
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
          <button type="button" className={`auth-tab ${mode === "signup" ? "auth-tab--on" : ""}`}
            onClick={() => setMode("signup")}>Create account</button>
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

        {error && <div className="auth-error">{error}</div>}

        <button type="submit" className="auth-submit" disabled={busy}>
          {busy ? "Working…" : (mode === "login" ? "Sign in" : "Create account")}
        </button>

        <p className="auth-hint">
          {mode === "login"
            ? "Stays signed in for 90 days on this device."
            : "Your username can be anything 3–30 chars. No email needed."}
          {" "}Local bookmarks/progress sync to your account automatically.
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
