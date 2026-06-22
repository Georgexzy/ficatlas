"use client"
import Link from "next/link"
import { useEffect, useState, ReactNode, MouseEvent } from "react"

// A navigation link that behaves like Next's <Link> when online, but performs a
// real full-page navigation when offline. This matters because Next's client
// router fetches RSC payloads over the network on navigation — which fails with
// no connection, leaving you stuck on the current page. A hard navigation
// triggers a document request the service worker can serve from cache instead,
// so you can still reach (e.g.) the Library's Offline tab while offline.
export default function OfflineLink(
  { href, children, className, onClick }:
  { href: string; children: ReactNode; className?: string; onClick?: () => void },
) {
  const [online, setOnline] = useState(true)
  useEffect(() => {
    const update = () => setOnline(navigator.onLine)
    update()
    window.addEventListener("online", update)
    window.addEventListener("offline", update)
    return () => {
      window.removeEventListener("online", update)
      window.removeEventListener("offline", update)
    }
  }, [])

  if (online) {
    return <Link href={href} className={className} onClick={onClick}>{children}</Link>
  }
  // Offline: hard navigation so the service worker serves the cached page.
  const handle = (e: MouseEvent) => {
    e.preventDefault()
    onClick?.()
    window.location.href = href
  }
  return <a href={href} className={className} onClick={handle}>{children}</a>
}
