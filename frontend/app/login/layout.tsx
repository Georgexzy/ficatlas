import type { Metadata } from "next"

// Metadata for a client-rendered route — see app/follows/layout.tsx. /login was
// the last of these still going out with the HOME page's title and description
// and no canonical, which is the shape Search Console reports as "duplicate
// without user-selected canonical". robots.txt disallows /login, so Google
// should never fetch it; this is for the crawlers and link previews that do.
export const metadata: Metadata = {
  title: "Sign in",
  robots: { index: false, follow: true },
}

export default function LoginLayout({ children }: { children: React.ReactNode }) {
  return children
}
