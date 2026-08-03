"use client"
import { useEffect, useId, useRef, useState } from "react"

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
// So this is a real control: hover on pointing devices, TAP on touch ones,
// Enter/Space from the keyboard, Escape to dismiss, and a proper
// aria-describedby link so a screen reader reads the hint as the description
// of the thing it explains rather than as a stray "question mark" button.
export default function HelpTip(
  { children, label = "What's this?" }: { children: React.ReactNode; label?: string },
) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLSpanElement | null>(null)
  const id = useId()

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false) }
    // Tapping elsewhere dismisses. Without this a tooltip opened by tap on a
    // phone has no obvious way to close — there is nothing to "hover away" from.
    const onDown = (e: MouseEvent | TouchEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("keydown", onKey)
    document.addEventListener("mousedown", onDown)
    document.addEventListener("touchstart", onDown)
    return () => {
      document.removeEventListener("keydown", onKey)
      document.removeEventListener("mousedown", onDown)
      document.removeEventListener("touchstart", onDown)
    }
  }, [open])

  return (
    <span className="helptip" ref={wrapRef}>
      <button
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
      {open && (
        <span className="helptip__bubble" role="tooltip" id={id}>
          {children}
        </span>
      )}
    </span>
  )
}
