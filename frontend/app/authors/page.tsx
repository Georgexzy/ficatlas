import type { Metadata } from "next"
import Link from "next/link"
import SiteHeader from "../SiteHeader"
import HubFilter from "../HubFilter"

// The index of author hubs — the third crawlable root, alongside /fandoms
// and /ships. People search for a writer by name more often than for a plot;
// this page is how a crawler reaches those pages.
const INTERNAL_API = process.env.INTERNAL_API_URL || "http://backend:8000"

export const dynamic = "force-dynamic"

const CACHE_S = 86400

export const metadata: Metadata = {
  title: "Browse authors",
  alternates: { canonical: "/authors" },
  description:
    "Every author indexed on FicAtlas, from Archive of Our Own, FanFiction.net "
    + "and FictionAlley. Browse by author, then see their works across archives.",
}

interface AuthorSummary { slug: string; name: string; work_count: number }

async function fetchAuthors(): Promise<AuthorSummary[]> {
  try {
    const r = await fetch(`${INTERNAL_API}/api/authors?limit=50000`, {
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

// Top authors by engagement (hits+kudos+bookmarks+comments), from the landing
// API which orders by the precomputed author_hubs.engagement column.
async function fetchTopAuthors(): Promise<AuthorSummary[]> {
  try {
    const r = await fetch(`${INTERNAL_API}/api/hubs/landing`, {
      next: { revalidate: 3600 },
      headers: { "x-internal-render": process.env.INTERNAL_RENDER_TOKEN || "" },
      signal: AbortSignal.timeout(10000),
    })
    if (!r.ok) return []
    const data = await r.json()
    return data.author || []
  } catch {
    return []
  }
}

export default async function AuthorsIndex() {
  const [authors, topAuthors] = await Promise.all([fetchAuthors(), fetchTopAuthors()])

  return (
    <main id="main" className="hub-page hub">
      <SiteHeader current="browse" />
      <nav className="browse-switch" aria-label="Browse by">
        <Link href="/fandoms" className="browse-switch__item">Fandoms</Link>
        <Link href="/ships" className="browse-switch__item">Pairings</Link>
        <span className="browse-switch__item browse-switch__item--current"
          aria-current="page">Authors</span>
        <Link href="/tropes" className="browse-switch__item">Tropes</Link>
        <Link href="/series" className="browse-switch__item">Series</Link>
      </nav>
      <h1>Browse authors</h1>
      <p className="hub__lede">
        Explore {authors.length.toLocaleString()} authors from AO3, FanFiction.net and
        FictionAlley. Choose one to see their works across archives, or{" "}
        <Link href="/fandoms">browse fandoms</Link> instead.
      </p>

      {authors.length === 0 ? (
        <p className="hub__empty">
          Author pages aren't built yet. <Link href="/">Search the index</Link> instead.
        </p>
      ) : (
        <HubFilter
          items={authors}
          basePath="/author"
          label="author"
          hideTop
          featured={topAuthors.length > 0 ? (
            <>
              <h2 className="hub__heading">Most read authors</h2>
              <ul className="hub__grid">
                {topAuthors.slice(0, 20).map(a => (
                  <li key={a.slug}>
                    <Link href={`/author/${a.slug}`} className="hub__chip">
                      <span className="hub__chip-name" title={a.name}>
                        {a.name.length > 40 ? a.name.slice(0, 38) + "…" : a.name}
                      </span>
                      <span className="hub__chip-count">{a.work_count.toLocaleString()}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </>
          ) : undefined}
        />
      )}
    </main>
  )
}