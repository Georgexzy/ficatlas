"use client"

import { ReactNode } from "react"

// The link out to the archive, and the one measurement that says whether this
// site did its job.
//
// Everything else the traffic report records stops one step short. A search
// that returned 5,000 results and a story page that was opened both look like
// success, and neither can be told apart from a reader who took one look and
// left. Measured over the data so far: of 47 real browser sessions that
// searched, 24 went on to open a story — and what happened to those 24 after
// that was invisible.
//
// So the click through to AO3, FanFiction.net or FictionAlley is beaconed. It
// records the same three things a pageview records — a daily visitor hash, the
// story page, and a host — and nothing else. The host here is the DESTINATION
// rather than the referrer, which is the one difference and is documented at
// the endpoint.
//
// Fired on click and not on navigation, because target="_blank" means the page
// is never left: there is no unload to hang it on. keepalive anyway, so a
// reader who closes the tab in the same breath is still counted.
//
// It must never delay or intercept the click. No await, no preventDefault, no
// href rewriting through a redirector — the anchor stays an ordinary anchor, so
// middle-click, ctrl-click and "copy link address" all still do what they
// should, and a beacon that fails costs a number rather than a reader.
export default function ArchiveLink(
  { href, className, children }:
  { href: string; className?: string; children: ReactNode },
) {
  const beacon = () => {
    try {
      const fd = new FormData()
      fd.append("path", window.location.pathname)
      fd.append("ref", href)
      fd.append("kind", "out")
      fetch("/api/traffic/hit", { method: "POST", body: fd, keepalive: true })
        .catch(() => {})
    } catch { /* analytics may never break a link */ }
  }
  return (
    <a href={href} target="_blank" rel="noopener noreferrer"
       className={className} onClick={beacon} onAuxClick={beacon}>
      {children}
    </a>
  )
}
