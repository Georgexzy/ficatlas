import type { Metadata, Viewport } from "next"
import { AuthProvider } from "@/lib/auth"
import ServiceWorkerRegistration from "./ServiceWorkerRegistration"
import SiteFooter from "./SiteFooter"
import "./globals.css"

export const metadata: Metadata = {
  title: "FicAtlas — Search all fanfiction",
  description: "Find fanfiction across AO3, FF.net, and more in one search.",
  manifest: "/manifest.json",
}

// Proper mobile scaling — without this, phones render the page at desktop
// width and zoom out, making everything tiny. width=device-width fixes that.
// maximumScale is left unset so users can still pinch-zoom for accessibility.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0e0e10",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ServiceWorkerRegistration />
        <AuthProvider>
          {children}
          <SiteFooter />
        </AuthProvider>
      </body>
    </html>
  )
}
