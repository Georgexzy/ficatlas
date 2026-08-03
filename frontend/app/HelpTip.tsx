"use client"
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"

// A "?" that explains a control, in the style AO3 uses on its search form.
//
// The site already had 19 native `title=""` attributes doing this job, and a
// native title has three problems that matter here:
//
//   * it never appears on a touch device — there is no hover, so on a phone
//     the explanation simply does not exist;
//   * it takes about a second to appear and then times out on its own;
//   * it cannot be reached from the keyboard.
//
// The bubble is rendered through a PORTAL onto <body>, not inside the control
// it belongs to. That is not incidental: the filter sidebar is a scroll
// container (`overflow-y: auto`), and a scroll container CLIPS absolutely
// positioned descendants — so an in-place bubble was cut off, and the sidebar
// is 220px against a bubble up to 280px, so it was cut off badly. `position:
// fixed` alone does not escape it either, because the mobile drawer animates
// with `transform`, and a transformed ancestor becomes the containing block
// for fixed children. A portal sidesteps both.
//
// Coordinates are measured from the button and clamped to the viewport, so the
// bubble can never hang off an edge, and it flips below the control when there
// is no room above.
export default function HelpTip(
  { children, label = "What's this?" }: { children: React.ReactNode; label?: string },
) {
  const [open, setOpen] = useState(false)
  const [mounted, setMounted] = useState(false)
  const [pos, setPos] = useState<{ top: number; left: number; below: boolean } | null>(null)
  const btnRef = useRef<HTMLButtonElement | null>(null)
  const bubbleRef = useRef<HTMLDivElement | null>(null)
  const id = useId()

  useEffect(() => setMounted(true), [])

  const place = useCallback(() => {
    const btn = btnRef.current
    if (!btn) return
    const r = btn.getBoundingClientRect()
    const vw = window.innerWidth
    const vh = window.innerHeight
    const bw = Math.min(300, vw - 24)
    const bh = bubbleRef.current?.offsetHeight ?? 90
    const gap = 8

    // Above by default; flip below when the top would be off-screen.
    const below = r.top - bh - gap < 8
    const top = below ? r.bottom + gap : r.top - bh - gap
    // Centre on the button, then clamp so neither edge leaves the viewport.
    let left = r.left + r.width / 2 - bw / 2
    left = Math.max(12, Math.min(left, vw - bw - 12))
    setPos({ top: Math.max(8, Math.min(top, vh - bh - 8)), left, below })
  }, [])

  useLayoutEffect(() => { if (open) place() }, [open, place])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setOpen(false); btnRef.current?.focus() }
    }
    // Tapping elsewhere dismisses. Without this a tooltip opened by tap on a
    // phone has no obvious way to close — there is nothing to "hover away" from.
    const onDown = (e: MouseEvent | TouchEvent) => {
      const t = e.target as Node
      if (!btnRef.current?.contains(t) && !bubbleRef.current?.contains(t)) setOpen(false)
    }
    // The button moves when the sidebar scrolls, so the bubble has to follow —
    // capture:true catches scrolls on any ancestor, not just the window.
    const reflow = () => place()
    document.addEventListener("keydown", onKey)
    document.addEventListener("mousedown", onDown)
    document.addEventListener("touchstart", onDown)
    window.addEventListener("scroll", reflow, true)
    window.addEventListener("resize", reflow)
    return () => {
      document.removeEventListener("keydown", onKey)
      document.removeEventListener("mousedown", onDown)
      document.removeEventListener("touchstart", onDown)
      window.removeEventListener("scroll", reflow, true)
      window.removeEventListener("resize", reflow)
    }
  }, [open, place])

  return (
    <span className="helptip">
      <button
        ref={btnRef}
        type="button"
        className="helptip__btn"
        aria-label={label}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen(o => !o) }}
        // Hover is an enhancement on top of click, never the only way in.
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >?</button>

      {mounted && open && pos && createPortal(
        <div
          ref={bubbleRef}
          className={`helptip__bubble ${pos.below ? "helptip__bubble--below" : ""}`}
          role="tooltip"
          id={id}
          style={{ top: pos.top, left: pos.left }}
          // Keep it open while the pointer is on the bubble itself, so a link
          // or long hint inside stays readable.
          onMouseEnter={() => setOpen(true)}
          onMouseLeave={() => setOpen(false)}
        >
          {children}
          <button
            type="button"
            className="helptip__close"
            aria-label="Close hint"
            onClick={() => { setOpen(false); btnRef.current?.focus() }}
          >✕</button>
        </div>,
        document.body,
      )}
    </span>
  )
}
