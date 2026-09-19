"use client"

import { useCallback, useState } from "react"
import QueuePanel, { type Post } from "./QueuePanel"
import OutreachPanel from "./OutreachPanel"

/**
 * One screen for one job: find a post, answer it, move on.
 *
 * These were two tabs. Splitting them matched how they were BUILT — a
 * worklist and a search tool — rather than how they are used, and the seam
 * showed: answering a post meant opening it on Reddit, selecting its text,
 * switching tab and pasting text the other tab had already read once.
 *
 * The queue is the index and the finder is the page. Picking a post hands it
 * straight over; marking it answered clears the pane and returns the list.
 */
export default function OutreachTab() {
  const [picked, setPicked] = useState<Post | null>(null)
  const [waiting, setWaiting] = useState<number | null>(null)
  // The list as the queue currently has it, so finishing one post can hand
  // over the next without waiting for a round trip.
  const [order, setOrder] = useState<Post[]>([])

  // ANSWER, THEN THE NEXT ONE — rather than answer, then an empty pane and a
  // scroll back to the list to find where you were. The queue is a worklist
  // and a worklist that does not advance is a list you stop working.
  const advance = useCallback((id: string) => {
    const i = order.findIndex(p => p.id === id)
    const next = i >= 0 ? order[i + 1] ?? order[i - 1] ?? null : null
    setPicked(next)
  }, [order])

  const onAnswered = useCallback(async (id: string) => {
    try {
      await fetch(`/api/queue/${encodeURIComponent(id)}/state`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: "answered" }),
      })
    } catch { /* the pane still advances; the list reloads on its own */ }
    advance(id)
  }, [advance])

  return (
    <>
      <h1 className="settings-title">
        Outreach
        {waiting != null && waiting > 0 && (
          <span className="outreach__waiting">{waiting} waiting</span>
        )}
      </h1>
      <p className="admin-note">
        Posts from the Lost Fic and Fic Search flairs, read through the same
        extractor as the box on the right. Nothing is posted anywhere — pick a
        post, refine the search, copy the reply, and send it yourself.
      </p>

      <div className="outreach-split">
        <aside className="outreach-split__queue">
          <QueuePanel selectedId={picked?.id} onPick={setPicked}
                      onCount={setWaiting} onList={setOrder}
                      onDone={advance} />
        </aside>
        <section className="outreach-split__work">
          {/* Keyed on the post so switching to another one resets the search
              rather than leaving the previous answer's results underneath a
              new question. */}
          <OutreachPanel key={picked?.id ?? "blank"} post={picked}
                         onAnswered={onAnswered} />
        </section>
      </div>
    </>
  )
}
