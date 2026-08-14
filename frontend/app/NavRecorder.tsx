"use client"

import { useEffect, useRef } from "react"
import { usePathname } from "next/navigation"
import { recordPath } from "@/lib/navStack"

// Writes down every page this tab visits, so "Back" can mean something — and
// tells the server that a page was viewed, so the site knows what it is used for.
//
// Mounted once in the root layout rather than per page: a page that forgot to
// record itself would be invisible to the stack, and the bug that produces —
// Back skipping a page you definitely visited — is worse and harder to spot
// than the one this fixes. Renders nothing.
//
// The pageview is reported from here rather than read off the server's request
// log because this is the only place that knows a page was actually RENDERED.
// The server sees prefetches, asset fetches and crawlers, and telling those
// apart from a person reading something would mean keeping more about each
// request, not less. See backend/tracking.py for what is stored — no address,
// no user agent, no account.
export default function NavRecorder() {
  const pathname = usePathname()
  // Guards the double-invoked effect in development's StrictMode, and a
  // re-render that does not actually change the path, from counting twice.
  const sent = useRef<string | null>(null)

  useEffect(() => {
    if (!pathname) return
    recordPath(pathname)

    // Honour Do Not Track. The numbers this feeds are for the operator's own
    // curiosity, which does not outrank somebody having said no.
    const dnt = typeof navigator !== "undefined" &&
      ((navigator as any).doNotTrack === "1" ||
       (navigator as any).doNotTrack === "yes" ||
       (window as any).doNotTrack === "1")
    if (dnt) return

    if (sent.current === pathname) return
    sent.current = pathname

    const fd = new FormData()
    fd.append("path", pathname)
    // Only an EXTERNAL referrer is worth sending: internal navigation would be
    // most of the rows and would say nothing about where readers come from.
    // Decided here because the browser is the only party that can see both
    // document.referrer and its own origin.
    try {
      const ref = document.referrer
      if (ref && new URL(ref).host !== location.host) fd.append("ref", ref)
    } catch { /* an unparseable referrer is simply not sent */ }

    // keepalive so the report survives the navigation that triggered it, and a
    // swallowed rejection because a page must never break over its own
    // analytics — offline, this simply does not happen.
    fetch("/api/traffic/hit", { method: "POST", body: fd, keepalive: true })
      .catch(() => {})
  }, [pathname])

  return null
}
