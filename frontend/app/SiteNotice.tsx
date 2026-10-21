"use client"

import { useEffect, useRef, useState } from "react"

const DISMISSED_KEY = "ficatlas:site-notice:dismissed"

/** Owner-authored, dismissible site message. It gives operators a way to explain
 * an incident or point readers at a new feature without shipping a frontend
 * change. The message itself is public settings data; only admins can write it. */
export default function SiteNotice() {
  const [message, setMessage] = useState("")
  const [noticeToken, setNoticeToken] = useState("")
  const [dismissed, setDismissed] = useState(false)
  const noticeRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    const node = noticeRef.current
    if (!node || !message || dismissed) {
      document.documentElement.style.removeProperty("--site-notice-height")
      return
    }
    const syncHeight = () => document.documentElement.style.setProperty(
      "--site-notice-height", `${Math.ceil(node.getBoundingClientRect().height)}px`)
    syncHeight()
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(syncHeight) : null
    observer?.observe(node)
    return () => {
      observer?.disconnect()
      document.documentElement.style.removeProperty("--site-notice-height")
    }
  }, [message, dismissed])

  useEffect(() => {
    let expiryTimer: number | undefined
    const update = (raw: unknown) => {
      const detail = raw && typeof raw === "object" ? raw as { message?: unknown; startsAt?: unknown; until?: unknown } : null
      const source = detail ? detail.message : raw
      let next = typeof source === "string" ? source.trim().slice(0, 500) : ""
      const until = detail && typeof detail.until === "string" ? detail.until : ""
      const startsAt = detail && typeof detail.startsAt === "string" ? detail.startsAt : ""
      const starts = startsAt ? Date.parse(startsAt) : NaN
      const expires = until ? Date.parse(until) : NaN
      const token = `${next}|${startsAt}|${until}`
      if (Number.isFinite(starts) && starts > Date.now()) next = ""
      if (Number.isFinite(expires) && expires <= Date.now()) next = ""
      setMessage(next)
      setNoticeToken(token)
      try { setDismissed(!!next && localStorage.getItem(DISMISSED_KEY) === token) } catch {}
      if (expiryTimer) window.clearTimeout(expiryTimer)
      if (next && Number.isFinite(expires) && expires > Date.now()) {
        expiryTimer = window.setTimeout(() => setMessage(""), expires - Date.now())
      }
      if (!next && Number.isFinite(starts) && starts > Date.now()) {
        expiryTimer = window.setTimeout(() => {
          update(detail)
          fetchNotice()
        }, Math.min(starts - Date.now() + 50, 2_147_483_647))
      }
    }
    let alive = true
    const fetchNotice = () => fetch("/api/settings", { credentials: "include", cache: "no-store" })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!alive) return
        update({ message: data?.site_notice, startsAt: data?.site_notice_start_at, until: data?.site_notice_until })
      })
      .catch(() => {})
    fetchNotice()
    // An operator can publish maintenance notice while readers already have a
    // chapter open. Poll gently so they see it before the restart without a
    // hard refresh; background tabs skip the request.
    const poll = window.setInterval(() => {
      if (document.visibilityState === "visible") fetchNotice()
    }, 20_000)
    const changed = (e: Event) => update((e as CustomEvent).detail)
    const visible = () => { if (document.visibilityState === "visible") fetchNotice() }
    window.addEventListener("ficatlas:site-notice-updated", changed)
    document.addEventListener("visibilitychange", visible)
    return () => {
      alive = false
      window.clearInterval(poll)
      if (expiryTimer) window.clearTimeout(expiryTimer)
      window.removeEventListener("ficatlas:site-notice-updated", changed)
      document.removeEventListener("visibilitychange", visible)
    }
  }, [])

  if (!message || dismissed) return null
  return (
    <aside ref={noticeRef} className="site-notice" role="status" aria-label="Site announcement">
      <strong className="site-notice__label">Site notice</strong>
      <span className="site-notice__message">{message}</span>
      <button type="button" className="site-notice__dismiss" aria-label="Dismiss announcement"
        onClick={() => {
          try { localStorage.setItem(DISMISSED_KEY, noticeToken) } catch {}
          setDismissed(true)
        }}>Dismiss</button>
    </aside>
  )
}
