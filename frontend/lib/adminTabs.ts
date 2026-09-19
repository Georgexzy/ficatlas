/**
 * Which admin area a `?tab=` value means.
 *
 * The admin page was five panels named after what had been built — Index
 * health, Takedown requests, Traffic, Posts to answer, Fic finder — and is now
 * four areas named after the work: Index, Audience, Outreach, Moderation.
 *
 * The old names still resolve, and this lives in lib/ rather than inside the
 * page so it can be tested. A rename that breaks its own inbound links is a
 * rename that gets reverted, and the links are in three places: `/takedowns`
 * redirects here, Settings links here, and an operator may have bookmarked
 * either. The redirect and the Settings link name the CURRENT tab — a 308 is
 * cached by the browser, so a stale target outlives the map that rescues it —
 * and this exists for the ones outside this repo's control.
 */
export type AdminTab = "index" | "audience" | "outreach" | "moderation"

const MOVED: Record<string, AdminTab> = {
  // What they are called now.
  index: "index",
  audience: "audience",
  outreach: "outreach",
  moderation: "moderation",
  // What they were called before the regrouping.
  health: "index",
  traffic: "audience",
  takedowns: "moderation",
  queue: "outreach",
}

/** The tab a `?tab=` value selects, or undefined to leave the default. */
export function adminTabFor(raw: string | null | undefined): AdminTab | undefined {
  if (!raw) return undefined
  return MOVED[raw.trim().toLowerCase()]
}
