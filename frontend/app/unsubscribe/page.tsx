"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { fetchWithTimeout, USER_TIMEOUT_MS } from "@/lib/net"

// Where the "stop these emails" link in a follow-update digest lands.
//
// The token is in the FRAGMENT (`/unsubscribe#t=…`), for the same reason the
// reset code is: a fragment never reaches a server log or a Referer header.
//
// It asks for a click rather than acting on load. Mail scanners and link
// previewers open every link in a message, and some run its JavaScript — an
// unsubscribe that fires on load switches readers off without their knowing.
// Mail clients' own Unsubscribe button does not come here at all: it POSTs to
// the API directly (RFC 8058).
export default function UnsubscribePage() {
  const [token, setToken] = useState<string | null>(null)
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle")

  useEffect(() => {
    const m = /(?:^#|&)t=([^&]+)/.exec(window.location.hash)
    setToken(m ? decodeURIComponent(m[1]) : "")
  }, [])

  const stop = async () => {
    if (!token) return
    setState("busy")
    try {
      const r = await fetchWithTimeout("/api/follows/email/unsubscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ t: token }),
      }, USER_TIMEOUT_MS)
      setState(r.ok ? "done" : "error")
    } catch {
      setState("error")
    }
  }

  return (
    <main id="main" className="page-prose">
      <h1>Stop update emails</h1>
      {token === null ? null : token === "" ? (
        <p className="page-prose__muted">
          This link is missing its code. You can turn update emails off from
          your <Link href="/library?tab=following">Following</Link> tab instead.
        </p>
      ) : state === "done" ? (
        <>
          <p>Done — you will not get any more update emails.</p>
          <p className="page-prose__muted">
            You are still following everything, and the{" "}
            <Link href="/library?tab=following">Following</Link> tab still shows
            what has new chapters. You can turn emails back on there.
          </p>
        </>
      ) : (
        <>
          <p>
            Stop the emails FicAtlas sends when a story you follow gets new
            chapters? Your follows themselves are kept.
          </p>
          <p>
            <button className="card-btn card-btn--primary" onClick={stop}
              disabled={state === "busy"}>
              {state === "busy" ? "Stopping…" : "Stop update emails"}
            </button>
          </p>
          {state === "error" && (
            <p className="takedown-form__error" role="alert">
              That did not go through. Try again, or turn emails off from your
              Following tab.
            </p>
          )}
        </>
      )}
    </main>
  )
}
