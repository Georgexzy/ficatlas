import type { Metadata } from "next"
import Link from "next/link"
import SiteHeader from "../SiteHeader"
import HubFilter from "../HubFilter"

// The index of fandom hubs — one crawlable page that reaches every hub, which
// in turn reaches the story pages. This is the root of the only path a search
// engine has into the index, since search URLs are blocked in robots.txt.
//
// It is also the page to hand someone who asks "what's actually in here?", a
// question the search box cannot answer because it needs you to already know
// what you are looking for.
const INTERNAL_API = process.env.INTERNAL_API_URL || "http://backend:8000"

// Rendered per request, not at build time.
//
// Next prerenders routes that declare `revalidate`, and a build runs inside a
// docker build layer where the `backend` host does not exist — the fetch hung
// until the 60s export timeout and failed the image three attempts running.
// Prerendering is also wrong on its own terms here: the data comes from a table
// rebuilt offline, so baking it into the image freezes it at whatever the last
// build saw.
//
// The cost is one query per request, and it is a small indexed read over ~5k
// hub rows rather than anything touching the 19.9M-row stories table. These are
// crawler-frequency routes, not the search path.
export const dynamic = "force-dynamic"

const CACHE_S = 86400

export const metadata: Metadata = {
  title: "Browse fandoms",
  alternates: { canonical: "/fandoms" },
  description:
    "Every fandom indexed on FicAtlas, from Archive of Our Own, FanFiction.net "
    + "and FictionAlley. Browse by fandom, then search within it.",
}

interface HubSummary { slug: string; name: string; work_count: number }

async function fetchHubs(): Promise<HubSummary[]> {
  try {
    const r = await fetch(`${INTERNAL_API}/api/hubs?limit=50000`, {
      next: { revalidate: CACHE_S },
      headers: { "x-internal-render": process.env.INTERNAL_RENDER_TOKEN || "" },
      signal: AbortSignal.timeout(15000),
    })
    if (!r.ok) return []
    return await r.json()
  } catch {
    return []
  }
}

export default async function FandomsIndex() {
  const hubs = await fetchHubs()

  return (
    <main id="main" className="hub-page hub">
      <SiteHeader current="browse" />
      <nav className="browse-switch" aria-label="Browse by">
        <span className="browse-switch__item browse-switch__item--current"
          aria-current="page">Fandoms</span>
        <Link href="/ships" className="browse-switch__item">Pairings</Link>
        <Link href="/authors" className="browse-switch__item">Authors</Link>
        <Link href="/tropes" className="browse-switch__item">Tropes</Link>
        <Link href="/series" className="browse-switch__item">Series</Link>
      </nav>
      <h1>Browse fandoms</h1>
      <p className="hub__lede">
        Explore {hubs.length.toLocaleString()} fandoms from AO3, FanFiction.net and
        FictionAlley. Choose one to browse its works, or{" "}
        <Link href="/ships">browse pairings</Link> instead.
      </p>

      {hubs.length === 0 ? (
        <p className="hub__empty">
          Fandom pages aren't built yet. <Link href="/">Search the index</Link> instead.
        </p>
      ) : (
        <HubFilter items={hubs} basePath="/fandom" label="fandom" />
      )}
    </main>
  )
}