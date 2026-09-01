import type { Metadata, Viewport } from "next"
import { AuthProvider } from "@/lib/auth"
import ServiceWorkerRegistration from "./ServiceWorkerRegistration"
import { Suspense } from "react"
import NavRecorder from "./NavRecorder"
import SiteFooter from "./SiteFooter"
import PreviewBanner from "./PreviewBanner"
import HealthBanner from "./HealthBanner"
import { escapeJsonLd } from "@/lib/jsonLd"
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
  // NO `alternates: { canonical: "/" }` here. It was here, and metadata in a
  // layout is INHERITED by every page under it that does not override it — so
  // every hub, every pairing and all ~20M story pages served
  //
  //     <link rel="canonical" href="https://ficatlas.com">
  //
  // which tells Google each of them is a duplicate of the home page and should
  // not be indexed in its own right. The entire point of the hubs is to be
  // indexed in their own right, so this quietly cancelled the whole exercise —
  // and it would have looked like "Google just hasn't ranked us yet".
  //
  // Absent is the safe default: with no canonical, Google self-canonicalises to
  // the URL it fetched, which is correct for every page here including the home
  // page. A WRONG canonical is destructive; a missing one is not. Set it
  // per-page (see the hub, story and index routes), never here.
  // `template` applies to pages that set their own title — a story page supplies
  // "Title — Author" and gets " · FicAtlas" appended, so a browser tab or a
  // shared link says whose site it is without the story page repeating it.
  // `default` is what every page without a title of its own still gets.
  title: {
    default: "FicAtlas — search AO3, FanFiction.net and FictionAlley at once",
    template: "%s · FicAtlas",
  },
  description:
    // "19+ million", not a precise figure: this string is baked at build time
    // and cannot read the live count the way the landing page does, and the
    // index only ever grows — so a "+" stays true indefinitely where "19.8M"
    // starts going stale the moment a worker adds a row.
    "Search 19+ million fanworks across Archive of Our Own, FanFiction.net and "
    + "FictionAlley in one place, then read them on the archive that hosts them. "
    + "No adverts, no tracking, no AI trained on fic.",
  manifest: "/manifest.json",
  // The site had NO favicon. /favicon.ico was a 404, and the only icons that
  // existed were the 192/512 PWA ones referenced from manifest.json — which a
  // browser tab, an address-bar suggestion and a Google result do not read. The
  // visible result was a generic globe everywhere the site was listed, which on
  // a search engine is the one place identity has to survive being one row in a
  // list of ten.
  //
  // favicon.ico carries 16/32/48 because browsers pick from inside it; the 48px
  // PNG is there because Google Search wants a favicon of 48x48 or a multiple,
  // from a stable crawlable URL (robots.txt allows these). Generated from the
  // existing FA mark, cropped past its 12.5% margin so the letters still read at
  // 16px — see the crop note where they are built.
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "16x16 32x32 48x48" },
      { url: "/icon-48.png", type: "image/png", sizes: "48x48" },
      { url: "/icon-192.png", type: "image/png", sizes: "192x192" },
    ],
    shortcut: "/favicon.ico",
    apple: { url: "/apple-icon.png", sizes: "180x180" },
  },
  // What a link to the site looks like when someone pastes it into Discord,
  // Reddit or a group chat, which for a site like this is most of how it
  // travels. Without these it renders as a bare URL. Deliberately reusing the
  // same title and description rather than writing a second, drifting copy.
  // One shared 1200x630 card for every page that does not supply its own.
  // Before this, a link shared anywhere rendered with no image at all; a card
  // is what makes a link worth clicking in Discord/Tumblr/a group chat, and
  // Open Graph image is also the one field every crawler reads even when the
  // page's body is a client-render shell. Committed as a real PNG (generated
  // once, image reproduced by tools/gen_og.py) rather than a generated route,
  // so crawlers that never execute JS still see it.
  openGraph: {
    type: "website",
    siteName: "FicAtlas",
    url: "/",
    title: "FicAtlas — search AO3, FanFiction.net and FictionAlley at once",
    description:
      "Search 19+ million fanworks across three archives in one place, then "
      + "read them on the archive that hosts them.",
    images: "/og.png",
  },
  twitter: {
    card: "summary_large_image",
    title: "FicAtlas — search AO3, FanFiction.net and FictionAlley at once",
    description:
      "Search 19+ million fanworks across three archives in one place, then "
      + "read them on the archive that hosts them.",
    images: "/og.png",
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
        {/* The stylesheet has carried `.skip-link` — off-screen until focused,
            with its own focus-visible ring — since the reader was built, and
            nothing ever rendered one. Without it a keyboard user tabs the whole
            header on every page before reaching the first word of anything.
            Every route's main region carries id="main" to land on. */}
        <a href="#main" className="skip-link">Skip to content</a>
        <ServiceWorkerRegistration />
        {/* Suspense because NavRecorder reads useSearchParams(), and a
            component that does cannot be statically prerendered — without this
            every static page (/about, /permissions, /takedown …) fails the
            build with "useSearchParams() should be wrapped in a suspense
            boundary". It renders nothing, so the boundary needs no fallback:
            the pages prerender as before and the recorder hydrates after. */}
        <Suspense fallback={null}>
          <NavRecorder />
        </Suspense>
        <AuthProvider>
          <HealthBanner />
          <PreviewBanner />
          {children}
          <SiteFooter />
        </AuthProvider>
        {/* WebSite schema: the entity Google associates with the domain and,
            via SearchAction, the site-wide searchbox shown as a sitelink. The
            query URL is the site's real search entry point. */}
        <script type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: escapeJsonLd({
            "@context": "https://schema.org",
            "@type": "WebSite",
            name: "FicAtlas",
            url: SITE,
            description: "Search 19+ million fanworks across Archive of Our Own, FanFiction.net and FictionAlley.",
            potentialAction: {
              "@type": "SearchAction",
              target: {
                "@type": "EntryPoint",
                urlTemplate: `${SITE}/?q={search_term_string}`,
              },
              "query-input": "required name=search_term_string",
            },
          }) }} />
      </body>
    </html>
  )
}
