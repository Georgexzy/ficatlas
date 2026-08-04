"use client"

import { useAuth } from "@/lib/auth"
import { useState } from "react"

// Shown on every page while a role preview is active.
//
// Deliberately loud. The failure mode this guards against is not subtle: you
// switch to reader to check a gate, forget, and then spend twenty minutes
// concluding that importing is broken. A quiet indicator would not prevent
// that, so this is a full-width bar in the accent colour with the exit sitting
// inside it.
export default function PreviewBanner() {
  const { user, refresh } = useAuth() as any
  const [busy, setBusy] = useState(false)
  if (!user?.previewing) return null

  const exit = async () => {
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append("role", "")
      await fetch("/api/auth/view-as", { method: "POST", body: fd, credentials: "include" })
      // Full reload rather than a state refresh: the role decides what half the
      // app renders, and a stale tree that thinks it is still a reader is the
      // same confusion this banner exists to prevent.
      window.location.reload()
    } catch {
      setBusy(false)
    }
  }

  return (
    <div className="preview-banner" role="status">
      <span>
        Viewing as <strong>{user.role}</strong>. You are still signed in as
        yourself — only what this session may do is limited.
      </span>
      <button onClick={exit} disabled={busy} className="preview-banner__exit">
        {busy ? "Leaving…" : "Leave preview"}
      </button>
    </div>
  )
}
