import type { Metadata } from "next"
import Link from "next/link"
import SiteHeader from "../SiteHeader"
import HubFilter from "../HubFilter"

// The series index — 347k multi-work series with no way to browse them.
// Each entry links to a real /series/{uuid} page with works in reading order.
const INTERNAL_API = process.env.INTERNAL_API_URL || "http://backend:8000"

export const dynamic = "force-dynamic"

const CACHE_S = 86400

export const metadata: Metadata = {
  title: "Browse series",
  alternates: { canonical: "/series" },
  description:
    "Every multi-work series indexed on FicAtlas, from Archive of Our Own, "
    + "FanFiction.net and FictionAlley. Browse by series, then read in order.",
}

interface SeriesSummary {
  slug: string
  name: string
  author: string | null
  work_count: number
  total_words: number | null
}

async function fetchSeries(): Promise<SeriesSummary[]> {
  try {
    const r = await fetch(
      `${INTERNAL_API}/api/stories/series?with_names=true&limit=5000`,
      {
        next: { revalidate: CACHE_S },
        headers: { "x-internal-render": process.env.INTERNAL_RENDER_TOKEN || "" },
        signal: AbortSignal.timeout(15000),
      },
    )
    if (!r.ok) return []
    return await r.json()
  } catch {
    return []
  }
}

export default async function SeriesIndex() {
  const series = await fetchSeries()

  // HubFilter expects {slug, name, work_count}; series use UUID slugs.
  const items = series.map(s => ({
    slug: s.slug,
    name: s.author ? `${s.name} — ${s.author}` : s.name,
    work_count: s.work_count,
  }))

  // Top 20 by total word count — the longest series to sink into.
  const longest = [...series]
    .sort((a, b) => (b.total_words || 0) - (a.total_words || 0))
    .slice(0, 20)

  return (
    <main id="main" className="hub-page hub">
      <SiteHeader current="browse" />
      <nav className="browse-switch" aria-label="Browse by">
        <Link href="/fandoms" className="browse-switch__item">Fandoms</Link>
        <Link href="/ships" className="browse-switch__item">Pairings</Link>
        <Link href="/authors" className="browse-switch__item">Authors</Link>
        <Link href="/tropes" className="browse-switch__item">Tropes</Link>
        <span className="browse-switch__item browse-switch__item--current"
          aria-current="page">Series</span>
      </nav>
      <h1>Browse series</h1>
      <p className="hub__lede">
        Explore {series.length.toLocaleString()} multi-work series from AO3, FanFiction.net and
        FictionAlley. Choose one to read in order, or{" "}
        <Link href="/fandoms">browse fandoms</Link> instead.
      </p>

      {series.length === 0 ? (
        <p className="hub__empty">
          Series pages aren't built yet. <Link href="/">Search the index</Link> instead.
        </p>
      ) : (
        <HubFilter
          items={items}
          basePath="/series"
          label="series"
          hideTop
          featured={
            <>
              <h2 className="hub__heading">Longest series</h2>
              <ul className="hub__grid">
                {longest.map(s => (
                  <li key={s.slug}>
                    <Link href={`/series/${s.slug}`} className="hub__chip">
                      <span className="hub__chip-name" title={s.name}>
                        {s.name.length > 40 ? s.name.slice(0, 38) + "…" : s.name}
                      </span>
                      <span className="hub__chip-count">
                        {s.total_words ? `${Math.round(s.total_words / 1000)}k words` : `${s.work_count} works`}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </>
          }
        />
      )}
    </main>
  )
}
