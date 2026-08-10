"use client"
import { useRouter } from "next/navigation"
import { ReactNode, MouseEvent } from "react"
import { navigateTo } from "@/lib/navigation"

// A link that actually navigates.
//
// This used to render a plain next/link whenever `navigator.onLine` was true,
// and only fall back to a document navigation when the browser admitted it was
// offline. That covers the easy half of the problem. The half it missed is the
// one people actually hit: the device has a connection, the backend is slow,
// wedged or restarting, and the client router's RSC fetch simply never returns.
// The link is not broken and reports no error — it just does nothing, forever.
//
// navigateTo handles both, so the online and offline paths are now the same code
// rather than two behaviours that had to be kept in agreement. Rendering a real
// <a href> in both cases matters too: middle-click, ctrl-click, "open in new
// tab" and the browser's own status bar all work, and none of them did when the
// offline branch swapped in an <a> only after mount.
export default function OfflineLink(
  { href, children, className, onClick, title, ariaLabel }:
  {
    href: string
    children: ReactNode
    className?: string
    onClick?: () => void
    title?: string
    ariaLabel?: string
  },
) {
  const router = useRouter()

  const handle = (e: MouseEvent<HTMLAnchorElement>) => {
    // Leave the modified clicks to the browser — they mean "not here".
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    onClick?.()
    navigateTo(h => router.push(h), href)
  }

  return (
    <a href={href} className={className} onClick={handle} title={title} aria-label={ariaLabel}>
      {children}
    </a>
  )
}
