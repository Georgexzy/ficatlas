import type { Metadata } from "next"

// Metadata for a client-rendered route, which cannot export its own.
//
// This one is a reader's private list of followed works: there is nothing here
// for a search engine, and until now it went out carrying the home page's title
// and description with no canonical — one of the four URLs Search Console
// grouped as "duplicate without user-selected canonical".
//
// noindex rather than a robots.txt Disallow, deliberately. Blocking the crawl
// stops the page being FETCHED, which also stops anyone reading the noindex, so
// a URL already known to Google can sit in the index indefinitely on the
// strength of its links alone. Letting it be fetched and told not to index is
// the instruction that actually removes it. `follow` costs nothing: the links
// on it point at story pages that should be crawled.
//
// A layout rather than a wrapper page because this route has no children, so
// there is nothing for the metadata to leak onto — the trap described in the
// root layout's canonical note.
export const metadata: Metadata = {
  title: "Works you follow",
  robots: { index: false, follow: true },
}

export default function FollowsLayout({ children }: { children: React.ReactNode }) {
  return children
}
