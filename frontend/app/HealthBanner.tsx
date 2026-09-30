"use client"

import { useEffect, useRef, useState } from "react"
import { describeError, type FailureKind } from "@/lib/errors"

// A bar that appears when the site cannot reach its own index.
//
// Before this, a backend that was down looked like a site with no results. The
// search page rendered its empty state — "no stories matched" — which is a
// confident, wrong answer: it tells the reader their search was fine and simply
// found nothing, when in fact nothing was searched. That is worse than an error,
// because they change the query and try again, and it keeps not working.
//
// Deliberately quiet about transient failures. One missed poll is a restart or a
// dropped packet, and a banner that flickers on every hiccup teaches people to
// ignore it. It takes two consecutive failures to appear and one success to go.
const POLL_MS = 30_000
const FAILURES_BEFORE_ALARM = 2

export default function HealthBanner() {
  const [state, setState] = useState<{ kind: FailureKind; message: string } | null>(null)
  const fails = useRef(0)

  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout>
    let last = 0

    const check = async () => {
      // CANCEL THE PENDING POLL BEFORE STARTING ONE.
      //
      // `check` re-arms itself in its own `finally`, and `wake` below calls it
      // directly on focus/online. Without this line that direct call did not
      // replace the scheduled poll, it ADDED to it: the pending timeout still
      // fired and started a second self-re-arming chain, and the two never
      // merged. One chain per focus event, for the life of the tab.
      //
      // Measured at the origin before the fix: 7,294 requests to
      // /api/stats/totals in a single hour — two a second, from a handful of
      // readers with tabs open — against the two a minute one chain costs.
      // None of it is absorbed at the edge, because this deliberately asks for
      // `no-store` (a health check served from a 300s cache cannot see an
      // outage), so every one reached the box over a domestic connection and
      // queued beside real searches. On a phone it is also somebody's battery
      // and data.
      clearTimeout(timer)
      last = Date.now()
      try {
        // Cheap and cached server-side. A health check that costs real work is
        // one that makes the outage worse.
        const ctl = new AbortController()
        const t = setTimeout(() => ctl.abort(), 12_000)
        const r = await fetch("/api/stats/totals", { signal: ctl.signal, cache: "no-store" })
        clearTimeout(t)
        if (!r.ok) throw describeError(null, r.status)
        if (!alive) return
        fails.current = 0
        setState(null)
      } catch (e) {
        if (!alive) return
        fails.current += 1
        if (fails.current >= FAILURES_BEFORE_ALARM) {
          const f = (e && typeof e === "object" && "kind" in e)
            ? (e as any) : describeError(e)
          setState({ kind: f.kind, message: f.message })
        }
      } finally {
        if (alive) timer = setTimeout(check, POLL_MS)
      }
    }

    check()
    // Check immediately when the connection returns or the tab is looked at
    // again, so the banner clears the moment it is wrong rather than up to
    // thirty seconds later.
    // Alt-tabbing is not news. Coming back after a while is, so this still
    // checks on focus — it just will not turn a flurry of window switches into
    // a flurry of requests. The banner clearing promptly is the point; doing it
    // more than once every few seconds buys nothing.
    const WAKE_MIN_GAP_MS = 5_000
    const wake = () => {
      if (Date.now() - last < WAKE_MIN_GAP_MS) return
      fails.current = 0
      check()
    }
    window.addEventListener("online", wake)
    window.addEventListener("focus", wake)
    const offline = () => setState({
      kind: "offline",
      message: "You are offline. Stories saved to this device are still readable from your Library.",
    })
    window.addEventListener("offline", offline)
    return () => {
      alive = false
      clearTimeout(timer)
      window.removeEventListener("online", wake)
      window.removeEventListener("focus", wake)
      window.removeEventListener("offline", offline)
    }
  }, [])

  if (!state) return null

  return (
    <div className={`health-banner health-banner--${state.kind}`} role="status" aria-live="polite">
      <span className="health-banner__dot" aria-hidden="true" />
      <span>{state.message}</span>
      {state.kind !== "offline" && (
        <button className="health-banner__retry"
          onClick={() => window.location.reload()}>Reload</button>
      )}
    </div>
  )
}
