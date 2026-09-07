import { permanentRedirect } from "next/navigation"

// Merged into /permissions, which now does both jobs in one flow: identify, see
// what is held, decide, and prove it only when granting something.
//
// Kept as a redirect rather than deleted, because this path is linked from
// About, the takedown form and the foot of every story page — and because an
// author may have bookmarked it, which is exactly the person least deserving of
// a 404. Query parameters are carried across so an inbound link that already
// knows who they are still skips the first step.
//
// A SERVER redirect, where this used to be a client component that rendered
// "Taking you to your works…" and then called router.replace in an effect. That
// shell answered 200 with no content of its own, the home page's title and no
// canonical — a page that is not a page, sitting in the index as a duplicate of
// every other client-rendered route here. 308 rather than 307 because the merge
// is not going to be undone.
export default async function ManageRedirect(
  { searchParams }: {
    searchParams: Promise<Record<string, string | string[] | undefined>>
  },
) {
  const sp = await searchParams
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(sp)) {
    if (Array.isArray(v)) v.forEach(x => qs.append(k, x))
    else if (v !== undefined) qs.set(k, v)
  }
  const q = qs.toString()
  permanentRedirect(q ? `/permissions?${q}` : "/permissions")
}
