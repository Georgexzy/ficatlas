"use client"

import Link from "next/link"
import { useState } from "react"

// Two steps on one page, because the second half is useless without the first
// and bouncing between routes loses the code people have just been given.
//
// The copy avoids promising an email will arrive. This site may have no mail
// transport at all (see api/password_reset.py), and telling someone to "check
// your inbox" when nothing was ever sent is how a reset flow becomes a support
// ticket instead of solving one.
export default function ForgotPassword() {
  const [step, setStep] = useState<"ask" | "enter">("ask")
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)

  async function request(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault(); setBusy(true); setError(null)
    try {
      const r = await fetch("/api/auth/forgot", { method: "POST", body: new FormData(e.currentTarget) })
      const d = await r.json()
      setNote(d.message); setStep("enter")
    } catch { setError("Could not reach the server. Please try again.") }
    finally { setBusy(false) }
  }

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault(); setBusy(true); setError(null)
    try {
      const r = await fetch("/api/auth/reset", { method: "POST", body: new FormData(e.currentTarget) })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "That did not work.")
      setDone(true)
    } catch (err: any) { setError(err.message) }
    finally { setBusy(false) }
  }

  if (done) {
    return (
      <div className="page-prose">
        <h1>Password changed</h1>
        <p>You can sign in with your new password now.</p>
        <p className="page-prose__muted">
          Any devices that were signed in have been signed out, so nobody who had
          the old password still has access.
        </p>
        <p><Link href="/login" className="card-btn card-btn--primary">Sign in</Link></p>
      </div>
    )
  }

  return (
    <div className="page-prose">
      <p className="page-prose__back"><Link href="/login">← Back to sign in</Link></p>
      <h1>Reset your password</h1>

      {step === "ask" && (
        <>
          <p>Tell us which account, and we&apos;ll create a reset code for it.</p>
          <form onSubmit={request} className="takedown-form">
            <label>
              <span>Username</span>
              <input name="username" required autoFocus autoComplete="username" />
            </label>
            {error && <p className="takedown-form__error">{error}</p>}
            <button type="submit" className="card-btn card-btn--primary" disabled={busy}>
              {busy ? "Working…" : "Create a reset code"}
            </button>
          </form>
          <p className="page-prose__muted" style={{ marginTop: 24 }}>
            Already have a code?{" "}
            <button className="linklike" onClick={() => setStep("enter")}>Enter it here</button>.
          </p>
        </>
      )}

      {step === "enter" && (
        <>
          {note && <p>{note}</p>}
          <p className="page-prose__muted">
            If you did not add an email address to your account, ask whoever runs
            this site for your code — they can create one for you.
          </p>
          <form onSubmit={submit} className="takedown-form">
            <label>
              <span>Reset code</span>
              <input name="code" required autoFocus placeholder="Paste the code you were given" />
            </label>
            <label>
              <span>New password</span>
              <input name="new_password" type="password" required minLength={6}
                autoComplete="new-password" placeholder="At least 6 characters" />
            </label>
            {error && <p className="takedown-form__error">{error}</p>}
            <button type="submit" className="card-btn card-btn--primary" disabled={busy}>
              {busy ? "Changing…" : "Set new password"}
            </button>
          </form>
        </>
      )}
    </div>
  )
}
