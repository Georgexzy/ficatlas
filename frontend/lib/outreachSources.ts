// Where an outreach post came from, and how a reply there has to look.
//
// The worklist outgrew Reddit (backend/outreach_sources.py): Bluesky asks and
// Tumblr fic-finder posts arrive in the same queue, read by the same
// extractor. What differs is the PLATFORM — its name, where a post is opened,
// and how long a reply may be — and that is all this file knows.

export type Source = "reddit" | "bluesky" | "tumblr"

export const SOURCE_LABEL: Record<Source, string> = {
  reddit: "Reddit", bluesky: "Bluesky", tumblr: "Tumblr",
}

/** Characters a reply may run to, or null when the platform sets no practical limit. */
export const REPLY_LIMIT: Record<Source, number | null> = {
  reddit: null,
  bluesky: 300,      // a Bluesky post is capped at 300 graphemes
  tumblr: null,
}

export function sourceOf(p: { source?: string | null }): Source {
  return (p.source === "bluesky" || p.source === "tumblr") ? p.source : "reddit"
}

/** "r/FanFiction", "Bluesky", "Tumblr · #fic finder". */
export function where(p: { source?: string | null; subreddit: string; flair?: string | null }): string {
  const s = sourceOf(p)
  if (s === "reddit") return `r/${p.subreddit}`
  if (s === "tumblr") return p.flair ? `Tumblr · #${p.flair}` : "Tumblr"
  return "Bluesky"
}

/**
 * The reply, fitted to the platform. Reddit and Tumblr take the full text.
 * Bluesky gets the shortest honest form — one line and the link — and, if even
 * that is over, the line is cut rather than the link: the link is the answer.
 */
export function fitReply(source: Source, full: string, short: string, link: string): string {
  const limit = REPLY_LIMIT[source]
  if (limit == null) return full
  const body = `${short} ${link}`
  if ([...body].length <= limit) return body
  const room = limit - [...link].length - 2
  return `${[...short].slice(0, Math.max(room, 0)).join("").trimEnd()}… ${link}`
}
