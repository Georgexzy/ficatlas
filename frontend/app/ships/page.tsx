import type { Metadata } from "next"
import Link from "next/link"
import SiteHeader from "../SiteHeader"
import HubFilter from "../HubFilter"

// The index of ship hubs — the second crawlable root, alongside /fandoms.
//
// Both exist because search URLs are blocked in robots.txt, so a crawler needs
// a bounded set of real pages to walk. /fandoms reaches the index by franchise;
// this reaches it by pairing, which is how readers more often think.
const INTERNAL_API = process.env.INTERNAL_API_URL || "http://backend:8000"

// Rendered per request, not at build time — see the note in ../fandoms/page.tsx.
// A build runs where the `backend` host does not resolve, and the data comes
// from a table rebuilt offline, so baking it into the image freezes it.
export const dynamic = "force-dynamic"

const CACHE_S = 86400

export const metadata: Metadata = {
  title: "Browse pairings",
  alternates: { canonical: "/ships" },
  description:
    "Every ship indexed on FicAtlas, across Archive of Our Own, FanFiction.net "
    + "and FictionAlley. Browse by pairing, then search within it.",
}

interface HubSummary { slug: string; name: string; work_count: number; nicknames?: string[] }

async function fetchShips(): Promise<HubSummary[]> {
  try {
    const r = await fetch(`${INTERNAL_API}/api/ships?limit=50000`, {
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

export default async function ShipsIndex() {
  const ships = await fetchShips()

  return (
    <main id="main" className="hub-page hub">
      <SiteHeader current="browse" />
      <nav className="browse-switch" aria-label="Browse by">
        <Link href="/fandoms" className="browse-switch__item">Fandoms</Link>
        <span className="browse-switch__item browse-switch__item--current"
          aria-current="page">Pairings</span>
        <Link href="/authors" className="browse-switch__item">Authors</Link>
        <Link href="/tropes" className="browse-switch__item">Tropes</Link>
        <Link href="/series" className="browse-switch__item">Series</Link>
      </nav>
      <h1>Browse pairings</h1>
      <p className="hub__lede">
        Explore {ships.length.toLocaleString()} pairings from AO3, FanFiction.net and
        FictionAlley. Choose one to browse its works, or{" "}
        <Link href="/fandoms">browse fandoms</Link> instead.
      </p>

      {ships.length === 0 ? (
        <p className="hub__empty">
          Pairing pages aren't built yet. <Link href="/">Search the index</Link> instead.
        </p>
      ) : (
        <HubFilter items={ships} basePath="/ship" label="pairing" />
      )}
    </main>
  )
}