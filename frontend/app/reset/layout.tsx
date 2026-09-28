import type { Metadata } from "next"

// Same reasoning as app/forgot/layout.tsx, and more pressing here: this URL is
// handed out in an email with a live reset code in the query string. It must
// never be indexed, and the code must never end up in a search engine's copy of
// the page.
//
// `follow: true` is deliberate and harmless — there is nothing to crawl from
// here but the sign-in link.
export const metadata: Metadata = {
  title: "Choose a new password",
  robots: { index: false, follow: true },
}

export default function ResetLayout({ children }: { children: React.ReactNode }) {
  return children
}
