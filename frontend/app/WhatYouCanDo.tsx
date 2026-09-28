"use client"

import Link from "next/link"
import { useEffect, useState } from "react"
import { useAuth } from "@/lib/auth"

/**
 * The three things this site can do that the archives cannot — said once, to
 * somebody who has just watched it work.
 *
 * WHY IT EXISTS. Measured over two weeks: 162 first-time readers, 25 back
 * within a week, 3 accounts. Acquisition is climbing and retention is not, and
 * the feature built for returning — one Following list spanning three archives,
 * which no single archive can offer — was invisible to every signed-out reader
 * until today. Nobody can want a feature they have never seen.
 *
 * WHERE, and this is most of the design. Not the landing page: that screen has
 * one job, which is to get a query typed, and this file already records what
 * happens when three lines of explanation are put next to the search box. Not a
 * modal, which interrupts the thing they came for. It appears under the RESULTS,
 * after a search has returned something — the one moment the reader has evidence
 * the site is worth an account, and the moment they are about to leave for the
 * archive.
 *
 * WHEN. Once. Dismissed for good, and never shown to somebody signed in. The
 * dismissal syncs, like the email prompt's, so it does not reappear on every
 * device — see the note in app/EmailPrompt.tsx about a prompt that returns after
 * you have answered it reading as the site not listening.
 *
 * Every line is a thing the code does, checked against it, for the same reason
 * app/WhyAccount.tsx gives: a list of promises that has drifted is worse than no
 * list, because this is where a reader decides whether the site is honest.
 */
const KEY = "ficatlas:tips-dismissed"

export default function WhatYouCanDo({ shown }: { shown: boolean }) {
  const { user } = useAuth()
  // After mount only: the value is in localStorage, and reading it during
  // render makes the server HTML and the first client render disagree.
  const [ready, setReady] = useState(false)
  const [hidden, setHidden] = useState(false)

  useEffect(() => {
    try { setHidden(localStorage.getItem(KEY) === "1") } catch { /* private mode */ }
    setReady(true)
  }, [])

  if (!ready || hidden || !shown || user) return null

  const dismiss = () => {
    setHidden(true)
    try { localStorage.setItem(KEY, "1") } catch { /* fine — it asks once more */ }
  }

  return (
    <aside className="tips" role="note" aria-label="What an account adds">
      <div className="tips__body">
        <p className="tips__lead">
          You can use all of this without an account. With one, it follows you:
        </p>
        <ul className="tips__list">
          <li>
            <strong>Know when a WIP updates.</strong> One list covering AO3,
            FanFiction.net and FictionAlley — the thing no single archive can do,
            because it only knows its own works. Nothing is emailed; updates are
            shown when you come back.
          </li>
          <li>
            <strong>Keep a search, not just a result.</strong> Save
            &ldquo;complete Drarry over 100k&rdquo; and re-run it whenever you
            like; it tells you what has appeared since you last looked.
          </li>
          <li>
            <strong>Your shelf and your place survive.</strong> Bookmarks and
            where you had got to in each story move between devices instead of
            living in one browser until you clear it.
          </li>
        </ul>
        <p className="tips__foot">
          <Link href="/login" className="tips__cta">Make an account</Link>
          <span className="tips__note">
            No email needed. Nothing is sold, and there are no adverts.
          </span>
        </p>
      </div>
      <button type="button" className="tips__x" onClick={dismiss}
        aria-label="Dismiss, and do not show this again">✕</button>
    </aside>
  )
}
