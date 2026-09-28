"use client"

import Link from "next/link"
import { useState } from "react"

// Two steps on one component, because the second half is useless without the
// first and bouncing between routes loses the code people have just been given.
//
// Mounted at BOTH /forgot and /reset, which are the two ways in and not two
// features. /forgot is where somebody arrives having forgotten; /reset is the
// address in the reset email, carrying ?code=, and it opens straight on the
// second step with the code already filled in. Building it twice is how they
// drift, and this one has a real cost if they do: the reset email links at
// /reset, so a divergence there locks people out rather than looking untidy.
//
// The copy avoids promising an email will arrive. This site may have no mail
// transport at all (see api/password_reset.py), and telling someone to "check
// your inbox" when nothing was ever sent is how a reset flow becomes a support
// ticket instead of solving one.
export default function ForgotClient({ initialCode = "" }: { initialCode?: string }) {
  // Straight to the code step when one arrived in the URL. A person following a
  // link from their own reset email has already done the first half; asking for
  // a username again would be asking them to prove something they just proved.
  const [step, setStep] = useState<"ask" | "enter">(initialCode ? "enter" : "ask")
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
          <p>
            Enter your username and a reset code will be created for the account.
            If it has an email address, the code is sent there.
          </p>
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
          {initialCode ? (
            <p>
              Choose a new password for your account. The code from your email is
              already filled in below.
            </p>
          ) : (
            <p className="page-prose__muted">
              If your account has no email address, request a code from{" "}
              <a href="mailto:help@ficatlas.com">help@ficatlas.com</a> and one
              will be issued to you.
            </p>
          )}
          <form onSubmit={submit} className="takedown-form">
            <label>
              <span>Reset code</span>
              {/* defaultValue, not value: this stays an uncontrolled input so
                  the form still reads it, and somebody who needs to correct a
                  truncated paste can still type over it. */}
              <input name="code" required autoFocus defaultValue={initialCode}
                placeholder="Paste the code you were given" />
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
