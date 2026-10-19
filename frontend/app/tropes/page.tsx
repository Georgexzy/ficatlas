import type { Metadata } from "next"
import Link from "next/link"
import SiteHeader from "../SiteHeader"
import HubFilter from "../HubFilter"

// The trope index — tropes aggregated across every fandom by tag.
// Each entry links to the most-popular fandom's hub page for that tag.
const INTERNAL_API = process.env.INTERNAL_API_URL || "http://backend:8000"

export const dynamic = "force-dynamic"

const CACHE_S = 86400

export const metadata: Metadata = {
  title: "Browse tropes",
  alternates: { canonical: "/tropes" },
  description:
    "Every trope indexed on FicAtlas, aggregated across Archive of Our Own, "
    + "FanFiction.net and FictionAlley. Browse by trope, then explore its works.",
}

interface TropeSummary { slug: string; name: string; work_count: number }

async function fetchTropes(): Promise<TropeSummary[]> {
  try {
    const r = await fetch(`${INTERNAL_API}/api/tropes?limit=50000`, {
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

export default async function TropesIndex() {
  const tropes = await fetchTropes()

  return (
    <main id="main" className="hub-page hub">
      <SiteHeader current="browse" />
      <nav className="browse-switch" aria-label="Browse by">
        <Link href="/fandoms" className="browse-switch__item">Fandoms</Link>
        <Link href="/ships" className="browse-switch__item">Pairings</Link>
        <Link href="/authors" className="browse-switch__item">Authors</Link>
        <span className="browse-switch__item browse-switch__item--current"
          aria-current="page">Tropes</span>
        <Link href="/series" className="browse-switch__item">Series</Link>
      </nav>
      <h1>Browse tropes</h1>
      <p className="hub__lede">
        Explore {tropes.length.toLocaleString()} tropes from AO3, FanFiction.net and
        FictionAlley, aggregated across every fandom. Choose one to explore its works,
        or <Link href="/fandoms">browse fandoms</Link> instead.
      </p>

      {tropes.length === 0 ? (
        <p className="hub__empty">
          Trope pages aren't built yet. <Link href="/">Search the index</Link> instead.
        </p>
      ) : (
        <HubFilter items={tropes} basePath="/fandom" label="trope" />
      )}
    </main>
  )
}