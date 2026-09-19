import { permanentRedirect } from "next/navigation"

// Merged into /admin, which now carries both owner surfaces as tabs. Kept as a
// redirect rather than deleted: it is linked from Settings and from the admin
// page's own copy, and an operator may have bookmarked it.
//
// A SERVER redirect, where this used to be a client component that rendered
// "Taking you to the queue…" and then called router.replace in an effect. That
// shell answered 200 with the layout's generic title, no canonical and no
// content — so to a crawler it was one more page identical to the home page and
// to every other client-rendered route here, which is how a page that is not
// really a page ended up in Search Console's duplicate report. It also cost a
// visible flash of placeholder text on the way through.
//
// permanentRedirect (308) rather than redirect (307), for the same reason as
// /s/<code>: this move is not going to be reversed, so a client is free to
// cache it. /admin is disallowed in robots.txt, so nothing follows it there.
//
// `moderation`, which is what that tab is called since the admin page was
// regrouped into four areas. It pointed at `takedowns` for a while after the
// rename and still worked, because the page maps the old names — but a 308 is
// CACHED BY THE BROWSER, so a stale target is one a reader keeps being sent to
// long after the compatibility map that rescues it has gone. The map is for
// links this repo does not control; its own links should name the real thing.
export default function TakedownsRedirect() {
  permanentRedirect("/admin?tab=moderation")
}
