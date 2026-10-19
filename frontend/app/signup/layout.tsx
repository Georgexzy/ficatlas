import type { Metadata } from "next"
import type { ReactNode } from "react"

export const metadata: Metadata = {
  title: "Create account · FicAtlas",
  description: "Create a free FicAtlas account to follow WIPs across AO3, FanFiction.net and FictionAlley, save searches, and keep your reading progress across devices. No email required.",
  robots: { index: false, follow: true },
}

export default function SignupLayout({ children }: { children: ReactNode }) {
  return children
}