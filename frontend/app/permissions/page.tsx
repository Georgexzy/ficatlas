import type { Metadata } from "next"
import PermissionsClient from "./PermissionsClient"

// A server wrapper whose only job is metadata, the same shape as the one over
// the story page.
//
// The page itself has to be a client component — it is a multi-step form that
// talks to the API — and a client component cannot export metadata. So this
// route went out with the ROOT LAYOUT's title and description, which is the
// home page's, and with no canonical, because the root layout deliberately sets
// none (see the note there). Search Console reported the result accurately:
// /permissions, /takedown, /follows and /forgot were four URLs carrying one
// identical title, one identical description and no statement about which was
// which — "duplicate without user-selected canonical", and none of them
// indexed.
//
// This is the page an author lands on when they want to say what may be done
// with their work, so it is worth being findable on its own terms.
export const metadata: Metadata = {
  title: "Author permissions",   // layout.tsx appends " · FicAtlas"
  description:
    "Tell FicAtlas what may be done with your fanworks: see what the index holds "
    + "under your name, restrict it, or allow the full text to be read here.",
  alternates: { canonical: "/permissions" },
}

export default function PermissionsPage() {
  return <PermissionsClient />
}
