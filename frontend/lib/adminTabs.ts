/**
 * Which admin area a `?tab=` value means.
 *
 * The admin page groups operational views by the work they serve, including
 * instance configuration, in ADMIN_GROUPS below.
 *
 * The old names still resolve, and this lives in lib/ rather than inside the
 * page so it can be tested. A rename that breaks its own inbound links is a
 * rename that gets reverted, and the links are in three places: `/takedowns`
 * redirects here, Settings links here, and an operator may have bookmarked
 * either. The redirect and the Settings link name the CURRENT tab — a 308 is
 * cached by the browser, so a stale target outlives the map that rescues it —
 * and this exists for the ones outside this repo's control.
 */
export type AdminTab =
  | "overview" | "configuration" | "tools" | "passes"
  | "coverage" | "crawler" | "storage"
  | "people" | "audience" | "outreach" | "moderation"

/** The rail, in order, grouped by the job each view is for. One list, so the
 *  rail, the `?tab=` parser and the tests cannot disagree about what exists. */
export const ADMIN_GROUPS: { title: string; tabs: { key: AdminTab; label: string }[] }[] = [
  { title: "Site", tabs: [
    { key: "overview", label: "Overview" },
    { key: "configuration", label: "Configuration" },
    { key: "tools", label: "Tools" },
    { key: "passes", label: "Passes" },
  ] },
  { title: "Index", tabs: [
    { key: "coverage", label: "Coverage" },
    { key: "crawler", label: "Crawler" },
    { key: "storage", label: "Storage" },
  ] },
  { title: "Readers", tabs: [
    { key: "audience", label: "Audience" },
    { key: "outreach", label: "Outreach" },
    { key: "people", label: "Accounts" },
  ] },
  { title: "Safety", tabs: [
    { key: "moderation", label: "Moderation" },
  ] },
]

const MOVED: Record<string, AdminTab> = {
  // What they are called now.
  ...Object.fromEntries(ADMIN_GROUPS.flatMap(g => g.tabs.map(t => [t.key, t.key]))),
  // "index" was the whole health page before it was split into views; its
  // nearest descendant is the overview, which is where that page's top half
  // (attention, tiles, jobs) now lives.
  index: "overview",
  accounts: "people",
  // What they were called before the first regrouping.
  health: "overview",
  traffic: "audience",
  takedowns: "moderation",
  queue: "outreach",
}

/** The tab a `?tab=` value selects, or undefined to leave the default. */
export function adminTabFor(raw: string | null | undefined): AdminTab | undefined {
  if (!raw) return undefined
  return MOVED[raw.trim().toLowerCase()]
}
