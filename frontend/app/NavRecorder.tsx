"use client"

import { useEffect, useRef } from "react"
import { usePathname, useSearchParams } from "next/navigation"
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
  // The QUERY matters to the back stack and to nothing else here.
  //
  // usePathname() returns the path without it, so a search — which lives
  // entirely in the query, `/?q=harry&page=3` — was recorded as bare `/`. That
  // is what made "Back to search" on a story page go to the HOME PAGE instead:
  // BackLink asks navStack where you came from, navStack said "/", and the
  // query and the page number you were on had never been written down.
  const searchParams = useSearchParams()
  // Guards the double-invoked effect in development's StrictMode, and a
  // re-render that does not actually change the path, from counting twice.
  const sent = useRef<string | null>(null)

  useEffect(() => {
    if (!pathname) return
    // Full URL for the back stack, so returning to a search returns to THAT
    // search — same filters, same page. navStack.isWithin() strips the query
    // before comparing, so a story page is still recognised as "inside" itself
    // regardless of what is recorded here.
    const qs = searchParams?.toString() ?? ""
    recordPath(qs ? `${pathname}?${qs}` : pathname)

    // Honour Do Not Track. The numbers this feeds are for the operator's own
    // curiosity, which does not outrank somebody having said no.
    const dnt = typeof navigator !== "undefined" &&
      ((navigator as any).doNotTrack === "1" ||
       (navigator as any).doNotTrack === "yes" ||
       (window as any).doNotTrack === "1")
    if (dnt) return

    if (sent.current === pathname) return
    // Whether this is the first page of the visit, not just a new path. The
    // referrer belongs to the DOCUMENT, and a client-side navigation does not
    // load a new one.
    const firstOfThisLoad = sent.current === null
    sent.current = pathname

    const fd = new FormData()
    // Deliberately the bare path, NOT the full URL. The traffic table is a
    // count of pages viewed, and putting the query string in it would start
    // recording what individual people searched for against a visitor hash —
    // a different and much more sensitive thing than this module collects.
    // Searches are already counted separately, without a visitor. See
    // backend/tracking.py.
    fd.append("path", pathname)
    // Only an EXTERNAL referrer, and only once per page load.
    //
    // document.referrer does not change across App Router navigations — they
    // are pushState, not a document load — so re-reading it on every path
    // change re-sent the same referrer for every page of the visit. Someone
    // arriving from reddit and reading twenty stories was counted as twenty
    // arrivals from reddit, and "where readers came from" overstated by roughly
    // pages-per-visit: the busier the visit, the bigger the lie.
    //
    // Decided here because the browser is the only party that can see both
    // document.referrer and its own origin.
    if (firstOfThisLoad) {
      try {
        const ref = document.referrer
        if (ref && new URL(ref).host !== location.host) fd.append("ref", ref)
      } catch { /* an unparseable referrer is simply not sent */ }
    }

    // keepalive so the report survives the navigation that triggered it, and a
    // swallowed rejection because a page must never break over its own
    // analytics — offline, this simply does not happen.
    fetch("/api/traffic/hit", { method: "POST", body: fd, keepalive: true })
      .catch(() => {})
  }, [pathname, searchParams])

  return null
}
