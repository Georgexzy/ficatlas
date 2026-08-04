import type { Metadata, Viewport } from "next"
import { AuthProvider } from "@/lib/auth"
import ServiceWorkerRegistration from "./ServiceWorkerRegistration"
import SiteFooter from "./SiteFooter"
import PreviewBanner from "./PreviewBanner"
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
      <head>
        {/* Runs before first paint. Setting the theme from React instead would
            render one frame in the wrong palette, and on a dark-by-default site
            that frame is a white flash — precisely what someone reading at
            night does not want. Deliberately tiny and dependency-free. */}
        <script dangerouslySetInnerHTML={{ __html: `
(function(){try{
  var m=localStorage.getItem('ficatlas:theme')||'system';
  var d=m==='dark'||(m==='system'&&window.matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.setAttribute('data-theme',d?'dark':'light');
}catch(e){document.documentElement.setAttribute('data-theme','dark');}})();
        `.trim() }} />
      </head>
      <body>
        <ServiceWorkerRegistration />
        <AuthProvider>
          <PreviewBanner />
          {children}
          <SiteFooter />
        </AuthProvider>
      </body>
    </html>
  )
}
