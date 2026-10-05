import type { Metadata } from "next"

// The address in every follow-update email, carrying the reader's unsubscribe
// token in the fragment. Never worth indexing.
export const metadata: Metadata = {
  title: "Stop update emails",
  robots: { index: false, follow: false },
}

export default function UnsubscribeLayout({ children }: { children: React.ReactNode }) {
  return children
}
