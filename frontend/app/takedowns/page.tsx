"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"

// Merged into /admin, which now carries both owner surfaces as tabs. Kept as a
// redirect rather than deleted: it is linked from Settings and from the admin
// page's own copy, and an operator may have bookmarked it.
export default function TakedownsRedirect() {
  const router = useRouter()
  useEffect(() => { router.replace("/admin?tab=takedowns") }, [router])
  return (
    <div className="page-prose">
      <p className="loading" role="status" aria-live="polite">Taking you to the queue…</p>
    </div>
  )
}
