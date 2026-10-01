"use client"

import Link from "next/link"
import { useEffect, useState } from "react"
import { fetchWithTimeout, USER_TIMEOUT_MS } from "@/lib/net"

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
export default function ForgotClient(
  { fromHash = false }: { fromHash?: boolean },
) {
  // The code arrives in the URL FRAGMENT, which only the browser can see — see
  // app/reset/page.tsx for why it is not a query parameter. Read after mount,
  // because `location` does not exist while the server renders, and cleared
  // THE HASH IS NOT CONSUMED HERE, and that is the fix for a real bug rather
  // than caution. Clearing it inside this effect worked exactly once: the hash
  // vanished from the address bar and the step never moved. The component is
  // mounted again after that first pass, and the second instance found the
  // fragment already gone, took the `return` below, and rendered the username
  // step — which is why the page looked untouched while the token had plainly
  // been read. Leaving the fragment in place makes the effect idempotent, so it
  // survives however many times React decides to run it.
  //
  // It is scrubbed on success instead (see submit), which is late enough to be
  // safe: a fragment never reaches a server, so the only exposure it has in the
  // meantime is the address bar of the person it was sent to.
  //
  // ONE effect that sets everything it needs to, rather than parking the code in
  // its own state and having a second effect watch it. That two-step version
  // read fine and did not work — the hash was consumed and the step never
  // moved — and the fix is not worth diagnosing when the indirection buys
  // nothing: there is exactly one caller and it wants all three values set at
  // the same moment.
  const [step, setStep] = useState<"ask" | "enter">("ask")
  const [code, setCode] = useState("")
  // Whether we arrived from a link in the email, which changes what the page
  // says — not merely whether the field is filled.
  const [fromEmail, setFromEmail] = useState(false)

  useEffect(() => {
    if (!fromHash) return
    const m = window.location.hash.match(/(?:^|[#&])code=([^&]+)/)
    if (!m) return
    setCode(decodeURIComponent(m[1]))
    setFromEmail(true)
    setStep("enter")
  }, [fromHash])

  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)

  async function request(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault(); setBusy(true); setError(null)
    try {
      const r = await fetchWithTimeout("/api/auth/forgot",
        { method: "POST", body: new FormData(e.currentTarget) }, USER_TIMEOUT_MS)
      const d = await r.json()
      setNote(d.message); setStep("enter")
    } catch { setError("Could not reach the server. Please try again.") }
    finally { setBusy(false) }
  }

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault(); setBusy(true); setError(null)
    try {
      const r = await fetchWithTimeout("/api/auth/reset",
        { method: "POST", body: new FormData(e.currentTarget) }, USER_TIMEOUT_MS)
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || "That did not work.")
      // Now the code is spent, take it out of the address bar so it is not left
      // in the history or in a screenshot of the confirmation page.
      if (window.location.hash) {
        window.history.replaceState(null, "", window.location.pathname)
      }
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
            Enter your username or your email address, and a reset code will be
            created for the account. If it has an address on file, the code is
            sent there.
          </p>
          <form onSubmit={request} className="takedown-form">
            <label>
              <span>Username or email address</span>
              {/* Either, because somebody who has forgotten their password has
                  usually also forgotten which they signed up with — and on this
                  site the address is optional and sign-in uses the username, so
                  the one a reader remembers is very often not the one the form
                  used to accept. The field name stays `username`: it is what the
                  endpoint has always received, and what changed is what that
                  endpoint will match. */}
              <input name="username" required autoFocus autoComplete="username"
                placeholder="Whichever you remember" />
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
          {fromEmail ? (
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
              {/* CONTROLLED, not defaultValue. The code is read after mount, and
                  defaultValue is only consulted on the first render — so the
                  field would have stayed empty on exactly the path this page
                  exists for. */}
              <input name="code" required autoFocus value={code}
                onChange={e => setCode(e.target.value)}
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
