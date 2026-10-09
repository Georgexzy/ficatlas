"use client"

import { useEffect, useState } from "react"

const DISMISSED_KEY = "ficatlas:site-notice:dismissed"

/** Owner-authored, dismissible site message. It gives operators a way to explain
 * an incident or point readers at a new feature without shipping a frontend
 * change. The message itself is public settings data; only admins can write it. */
export default function SiteNotice() {
  const [message, setMessage] = useState("")
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    const update = (raw: unknown) => {
      const next = typeof raw === "string" ? raw.trim().slice(0, 500) : ""
      setMessage(next)
      try { setDismissed(!!next && localStorage.getItem(DISMISSED_KEY) === next) } catch {}
    }
    let alive = true
    fetch("/api/settings", { credentials: "include", cache: "no-store" })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!alive) return
        update(data?.site_notice)
      })
      .catch(() => {})
    const changed = (e: Event) => update((e as CustomEvent).detail)
    window.addEventListener("ficatlas:site-notice-updated", changed)
    return () => { alive = false; window.removeEventListener("ficatlas:site-notice-updated", changed) }
  }, [])

  if (!message || dismissed) return null
  return (
    <aside className="site-notice" role="status" aria-label="Site announcement">
      <span className="site-notice__message">{message}</span>
      <button type="button" className="site-notice__dismiss" aria-label="Dismiss announcement"
        onClick={() => {
          try { localStorage.setItem(DISMISSED_KEY, message) } catch {}
          setDismissed(true)
        }}>Dismiss</button>
    </aside>
  )
}
