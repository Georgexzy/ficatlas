import type { Metadata } from "next"
import TakedownClient from "./TakedownClient"

// Server wrapper for metadata only — see app/permissions/page.tsx for why every
// client-rendered route here was going out with the home page's title and no
// canonical, and what Search Console made of that.
//
// The person who needs this page is an author who has found their work
// somewhere they did not put it. They are the last people who should have to
// find it through a site they are unhappy with, so it gets a title that says
// what it does and an address of its own.
export const metadata: Metadata = {
  title: "Remove a work",   // layout.tsx appends " · FicAtlas"
  description:
    "Ask for a story to be removed from the FicAtlas index. Four fields, no account "
    + "needed, and an answer that says what actually happened.",
  alternates: { canonical: "/takedown" },
}

export default function TakedownPage() {
  return <TakedownClient />
}
