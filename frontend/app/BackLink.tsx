"use client"

import Link from "next/link"
import { useRouter } from "next/navigation"
import { useEffect, useState } from "react"

// "Back", where back means something.
//
// The site had one-off back links on four pages out of ten, each hard-coded to
// a guess about where you came from — /about said "back to search", /takedown
// said "About FicAtlas" — so following About → Takedown and pressing back sent
// you somewhere you had never been.
//
// This uses actual history when there is any, and falls back to a named
// destination when there is not. The distinction matters: a page opened from a
// shared link, or as the first page of a session, has nothing behind it, and a
// "Back" that silently does nothing is worse than one that says where it goes.
//
// history.length is the only signal available — the Navigation API is not in
// Safari, and document.referrer is empty for same-origin client-side routes.
// It is read once after mount, because reading it during render would make the
// server and client disagree.
export default function BackLink(
  { fallback = "/", label = "Back", fallbackLabel }:
  { fallback?: string; label?: string; fallbackLabel?: string },
) {
  const router = useRouter()
  const [canGoBack, setCanGoBack] = useState(false)

  useEffect(() => {
    // > 1 means this tab has somewhere of its own to return to. A fresh tab
    // opened straight onto this page has length 1.
    setCanGoBack(typeof window !== "undefined" && window.history.length > 1)
  }, [])

  if (!canGoBack) {
    return (
      <p className="back-link">
        <Link href={fallback}>
          <span aria-hidden="true">←</span> {fallbackLabel ?? label}
        </Link>
      </p>
    )
  }

  return (
    <p className="back-link">
      <button type="button" onClick={() => router.back()}>
        <span aria-hidden="true">←</span> {label}
      </button>
    </p>
  )
}
