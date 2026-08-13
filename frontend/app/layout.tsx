import type { Metadata, Viewport } from "next"
import { AuthProvider } from "@/lib/auth"
import ServiceWorkerRegistration from "./ServiceWorkerRegistration"
import NavRecorder from "./NavRecorder"
import SiteFooter from "./SiteFooter"
import PreviewBanner from "./PreviewBanner"
import HealthBanner from "./HealthBanner"
import "./globals.css"

// What the site says it is, in the two places people meet it before they see it:
// a browser tab and a shared link.
//
// "Search all fanfiction" overpromised in one direction and undersold in the
// other. It is not all fanfiction — it is three archives, and naming them is
// both more accurate and more persuasive, because a reader who uses two of them
// immediately understands what the third buys. And "search" alone leaves out the
// half that matters: this finds work and sends you to the archive that hosts it.
// Baked at build time by the bundler, not read at runtime — see the note on
// NEXT_PUBLIC_SITE_URL in frontend/Dockerfile.
const SITE = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000"

export const metadata: Metadata = {
  // Without metadataBase, Next resolves every relative URL in this block against
  // localhost and says so in the build log. That makes the canonical link and
  // the Open Graph URL point at a host no crawler or chat client can reach, so a
  // shared link renders bare and Google is told the page's real address is one
  // it cannot fetch.
  metadataBase: new URL(SITE),
  alternates: { canonical: "/" },
  // `template` applies to pages that set their own title — a story page supplies
  // "Title — Author" and gets " · FicAtlas" appended, so a browser tab or a
  // shared link says whose site it is without the story page repeating it.
  // `default` is what every page without a title of its own still gets.
  title: {
    default: "FicAtlas — search AO3, FanFiction.net and FicAlley at once",
    template: "%s · FicAtlas",
  },
  description:
    // "19+ million", not a precise figure: this string is baked at build time
    // and cannot read the live count the way the landing page does, and the
    // index only ever grows — so a "+" stays true indefinitely where "19.8M"
    // starts going stale the moment a worker adds a row.
    "Search 19+ million fanworks across Archive of Our Own, FanFiction.net and "
    + "FicAlley in one place, then read them on the archive that hosts them. "
    + "No adverts, no tracking, no AI trained on fic.",
  manifest: "/manifest.json",
  // What a link to the site looks like when someone pastes it into Discord,
  // Reddit or a group chat, which for a site like this is most of how it
  // travels. Without these it renders as a bare URL. Deliberately reusing the
  // same title and description rather than writing a second, drifting copy.
  openGraph: {
    type: "website",
    siteName: "FicAtlas",
    url: "/",
    title: "FicAtlas — search AO3, FanFiction.net and FicAlley at once",
    description:
      "Search 19+ million fanworks across three archives in one place, then "
      + "read them on the archive that hosts them.",
  },
  twitter: {
    card: "summary",
    title: "FicAtlas — search AO3, FanFiction.net and FicAlley at once",
    description:
      "Search 19+ million fanworks across three archives in one place, then "
      + "read them on the archive that hosts them.",
  },
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
        <NavRecorder />
        <AuthProvider>
          <HealthBanner />
          <PreviewBanner />
          {children}
          <SiteFooter />
        </AuthProvider>
      </body>
    </html>
  )
}
