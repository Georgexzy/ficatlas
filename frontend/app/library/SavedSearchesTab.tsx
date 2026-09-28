"use client"

import Link from "next/link"
import { useEffect, useState } from "react"
import { loadSaved, removeSaved, SAVED_CHANGED, type SavedSearch } from "@/lib/savedSearches"

// Saved searches, in the Library with everything else that is yours.
//
// They had no home. The only way to reach one was to click into an empty search
// box and pick it out of the focus dropdown — fine for re-running the search you
// just made, useless for "what was that thing I kept in March", and invisible to
// anybody who had not already discovered the dropdown. A list you cannot browse
// is not a list.
//
// Here rather than in Settings, on the same argument that moved Following here:
// /library answers "what have I got?", and a standing question is something you
// have. Settings is where you change how the site behaves.
//
// The dropdown stays. It is the right surface for the common case — re-running
// one while your hands are already in the search box — and this is the surface
// for managing them.
function fmtDate(iso?: string): string {
  if (!iso) return ""
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? "" : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })
}

export default function SavedSearchesTab() {
  const [saved, setSaved] = useState<SavedSearch[] | null>(null)

  useEffect(() => {
    const read = () => setSaved(loadSaved())
    read()
    window.addEventListener(SAVED_CHANGED, read)
    // A sync pull can bring searches saved on another device; without this the
    // list sits stale until the tab is reopened.
    window.addEventListener("ficatlas:storage-pulled", read)
    return () => {
      window.removeEventListener(SAVED_CHANGED, read)
      window.removeEventListener("ficatlas:storage-pulled", read)
    }
  }, [])

  if (saved === null) return <p className="library-empty">Loading…</p>

  if (!saved.length) {
    return (
      <div className="library-empty">
        <p>No saved searches yet.</p>
        <p className="library-empty__hint">
          Run a search and press <strong>☆ Save search</strong> beside the
          result count. A saved search is a standing question — &ldquo;complete
          Drarry over 100k&rdquo; — and re-running it tells you what has
          appeared since you last looked.
        </p>
        <Link href="/" className="card-btn card-btn--primary">Start a search</Link>
      </div>
    )
  }

  return (
    <div className="library-list">
      {saved.map(s => (
        <div key={s.id} className="library-item">
          {/* The whole query string is the link, because the query IS the
              request — filters, status, word count and sort all travel in it,
              which is why a saved search is one string and not a record. */}
          {/* rel="nofollow" because this points into `/?…`, the search URL space
              robots.txt disallows — every URL in it is a query over 20.8M rows
              on a home server. tests/check-nofollow.py enforces it, and caught
              this one. */}
          <Link href={`/?q=${encodeURIComponent(s.q)}`} rel="nofollow"
            className="library-item__main">
            <p className="library-item__title library-item__title--query">{s.q}</p>
            <p className="library-item__meta">
              saved {fmtDate(s.at)}
              {s.last_total != null && <> · {s.last_total.toLocaleString()} works when last run</>}
              {s.last_run && <> · last run {fmtDate(s.last_run)}</>}
            </p>
          </Link>
          <button className="library-item__remove"
            aria-label={`Forget the saved search "${s.q}"`}
            onClick={() => setSaved(removeSaved(s.id))}>✕</button>
        </div>
      ))}
    </div>
  )
}
