/**
 * Which search result a reader opened, and at what position.
 *
 * The funnel says 29% of people who search open a work and cannot say why the
 * rest do not. Position is the missing half: clicks piling onto 1-3 mean the
 * right work is there and the rest of page one is noise, clicks spread thin or
 * absent mean the ranking is not putting it there. One delegated listener on
 * the results list sends a `click` beacon; the backend derives the search text
 * from `search` the same way its search logger does, so the two join.
 *
 * Only links that OPEN THE WORK count — its page here, or its archive page.
 * The tag, fandom and author links on a card start a new search; they are a
 * reader refining, not choosing.
 */
import { fetchBackground } from "./net"

const ARCHIVE_WORK = /(?:archiveofourown\.org\/works\/|fanfiction\.net\/s\/|fictionalley\.org\/authors\/)/i

export function opensTheWork(href: string | null | undefined): boolean {
  if (!href) return false
  if (href.startsWith("/story/")) return true
  return ARCHIVE_WORK.test(href)
}

/** The beacon's form fields, or null when this click is not a result opening. */
export function clickFields(href: string | null | undefined, storyId: string | undefined,
                            pos: number, search: string): Record<string, string> | null {
  if (!storyId || !(pos > 0) || !opensTheWork(href)) return null
  return { path: `/story/${storyId}`, kind: "click", pos: String(pos), search }
}

/** onClickCapture / onAuxClickCapture for the results list. Never throws. */
export function recordResultClick(e: { target: EventTarget | null }): void {
  try {
    const a = (e.target as Element | null)?.closest?.("a")
    const card = a?.closest?.("[data-pos]") as HTMLElement | null
    if (!a || !card) return
    const f = clickFields(a.getAttribute("href"), card.dataset.story,
                          Number(card.dataset.pos), window.location.search)
    if (!f) return
    const fd = new FormData()
    for (const [k, v] of Object.entries(f)) fd.append(k, v)
    fetchBackground("/api/traffic/hit", { method: "POST", body: fd, keepalive: true })
  } catch { /* analytics may never break a link */ }
}
