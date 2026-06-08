import type { Metadata } from "next"
import "./globals.css"

export const metadata: Metadata = {
  title: "FicAtlas — Search all fanfiction",
  description: "Find fanfiction across AO3, FF.net, and more in one search.",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
