"use client"

import Link from "next/link"
import { useEffect, useState } from "react"
import { useAuth } from "@/lib/auth"

// "You have no way back into this account."
//
// Signup never asked for an address, so every account this site has made began
// without one — and an account with no address cannot be recovered at all:
// there is nothing to send a reset to and nothing to check a claim against.
// Both live accounts are in that state, and one of them is not the operator's.
//
// PROMPTED, NEVER FORCED, which is the whole design of this component:
//
//   * it is dismissible, and a dismissal is remembered for good. Somebody who
//     has decided not to give an address has answered the question, and asking
//     again is not a reminder, it is nagging.
//   * it never blocks anything. No modal, no interstitial, no gate on any
//     feature — it is a line above the page with a link and an X.
//   * it disappears by itself the moment an address exists, so it cannot
//     survive being acted on.
//   * it says what an address is FOR and does not overclaim. Mail from a home
//     connection is routinely binned by the big providers, so this promises
//     that recovery becomes possible rather than that a reset will arrive; see
//     backend/api/password_reset.py, where a request without SMTP still creates
//     a code an operator can pass on by hand.
//
// Storage is localStorage rather than the account, deliberately: recording the
// dismissal on the server would mean writing to the user row of somebody who
// has just declined to give you something, and the setting is a per-device
// convenience rather than a fact about the person.
const KEY = "ficatlas:email-prompt-dismissed"

export default function EmailPrompt() {
  const { user } = useAuth() as any
  // Rendered only after mount: the value lives in localStorage, and reading it
  // during render would make the server HTML and the first client render
  // disagree.
  const [ready, setReady] = useState(false)
  const [hidden, setHidden] = useState(false)

  useEffect(() => {
    try { setHidden(localStorage.getItem(KEY) === "1") } catch { /* private mode */ }
    setReady(true)
  }, [])

  if (!ready || hidden) return null
  if (!user || user.email) return null

  const dismiss = () => {
    setHidden(true)
    try { localStorage.setItem(KEY, "1") } catch { /* fine — it just asks again */ }
  }

  return (
    <div className="email-prompt" role="status">
      <span className="email-prompt__text">
        This account has no email address, so there is no way to get back into it
        if you forget your password.{" "}
        <Link href="/account" className="email-prompt__link">Add one</Link> — it is
        used for nothing else.
      </span>
      <button type="button" className="email-prompt__x" onClick={dismiss}
        aria-label="Dismiss, and do not ask again">✕</button>
    </div>
  )
}
