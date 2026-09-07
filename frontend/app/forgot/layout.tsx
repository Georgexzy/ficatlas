import type { Metadata } from "next"

// Metadata for a client-rendered route — see app/follows/layout.tsx for the
// reasoning, which is the same here and more obviously right: a password reset
// form is not a page anybody should reach from a search engine, and it was
// going out with the home page's title, the home page's description and no
// canonical.
export const metadata: Metadata = {
  title: "Reset your password",
  robots: { index: false, follow: true },
}

export default function ForgotLayout({ children }: { children: React.ReactNode }) {
  return children
}
