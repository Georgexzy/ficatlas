"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"

// Merged into /permissions, which now does both jobs in one flow: identify, see
// what is held, decide, and prove it only when granting something.
//
// Kept as a redirect rather than deleted, because this path is linked from
// About, the takedown form and the foot of every story page — and because an
// author may have bookmarked it, which is exactly the person least deserving of
// a 404. Query parameters are carried across so an inbound link that already
// knows who they are still skips the first step.
export default function ManageRedirect() {
  const router = useRouter()
  useEffect(() => {
    const qs = window.location.search
    router.replace(`/permissions${qs}`)
  }, [router])

  return (
    <div className="page-prose">
      <p className="loading" role="status" aria-live="polite">
        Taking you to your works…
      </p>
    </div>
  )
}
