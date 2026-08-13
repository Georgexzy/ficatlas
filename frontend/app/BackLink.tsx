"use client"

import Link from "next/link"
import { useRouter, usePathname } from "next/navigation"
import { useEffect, useState } from "react"
import { previousOutside, truncateTo } from "@/lib/navStack"

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
  const pathname = usePathname()
  const [canGoBack, setCanGoBack] = useState(false)
  // Where back actually leads, from this tab's own record of where it has been.
  //
  // history.length alone said only "there is something behind you", never what.
  // On a story page that something is very often a chapter OF THIS STORY, which
  // is how closing a chapter and pressing Back put you straight back into it.
  // See lib/navStack.ts for why the browser cannot be asked.
  const [target, setTarget] = useState<string | null>(null)

  useEffect(() => {
    if (typeof window === "undefined") return
    const prev = pathname ? previousOutside(pathname) : null
    setTarget(prev)
    // Still gated on real history: with a recorded path but a fresh tab (a
    // restored session, say) router.back() has nothing to go to.
    setCanGoBack(window.history.length > 1 && prev !== null)
  }, [pathname])

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
      {/* push to the recorded destination rather than router.back(), because
          back is one step and the thing worth skipping is often several — a
          story visited from its own chapter list can have half a dozen entries
          inside it. The stack is trimmed to match so a second Back does not
          walk forward into what was just skipped. */}
      <button type="button" onClick={() => {
        if (target) { truncateTo(target); router.push(target) } else { router.back() }
      }}>
        <span aria-hidden="true">←</span> {label}
      </button>
    </p>
  )
}
