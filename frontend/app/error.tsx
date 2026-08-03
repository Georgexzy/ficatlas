"use client"

import Link from "next/link"
import { useEffect } from "react"

// Client-side crash boundary. Without one, an unhandled render error takes the
// whole route down to a blank page. The message itself is deliberately not
// shown: in production it can carry internal detail, and it means nothing to a
// reader — the console keeps the real error for whoever is debugging.
export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  useEffect(() => { console.error(error) }, [error])

  return (
    <div className="empty">
      <h1 className="empty__title">Something went wrong</h1>
      <p className="empty__sub">
        This page failed to load. Trying again often works — the index is large
        and some queries time out under load.
      </p>
      <div style={{ display: "flex", gap: 10, justifyContent: "center" }}>
        <button onClick={reset} className="card-btn card-btn--primary">Try again</button>
        <Link href="/" className="card-btn">Back to search</Link>
      </div>
    </div>
  )
}
