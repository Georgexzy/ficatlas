import type { Metadata } from "next"
import Link from "next/link"
import SiteHeader from "../SiteHeader"

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
  description:
    "Every ship indexed on FicAtlas, across Archive of Our Own, FanFiction.net "
    + "and FicAlley. Browse by pairing, then search within it.",
}

interface HubSummary { slug: string; name: string; work_count: number }

async function fetchShips(): Promise<HubSummary[]> {
  try {
    const r = await fetch(`${INTERNAL_API}/api/ships?limit=10000`, {
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

  const top = ships.slice(0, 60)

  // Grouped by the first letter of the pairing as displayed. The display name is
  // the most-used spelling rather than the alphabetical one the slug is built
  // from, so this groups by what a reader actually reads on the page.
  const groups = new Map<string, HubSummary[]>()
  for (const h of [...ships].sort((a, b) => a.name.localeCompare(b.name))) {
    const first = h.name[0]?.toUpperCase() ?? "#"
    const key = /[A-Z]/.test(first) ? first : "#"
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(h)
  }
  const letters = [...groups.keys()].sort()

  return (
    <div className="hub-page hub">
      <SiteHeader />
      <h1>Browse pairings</h1>
      <p className="hub__lede">
        {ships.length.toLocaleString()} pairings indexed across Archive of Our
        Own, FanFiction.net and FicAlley — every archive at once, which no single
        archive&rsquo;s tag page can do. Pick one to see its most popular works,
        browse <Link href="/fandoms">by fandom</Link> instead, or{" "}
        <Link href="/">search the whole index</Link> if you know what you want.
      </p>

      {ships.length === 0 ? (
        <p className="hub__empty">
          Pairing pages aren’t built yet. <Link href="/">Search the index</Link> instead.
        </p>
      ) : (
        <>
          <h2 className="hub__heading">Biggest pairings</h2>
          <ul className="hub__grid">
            {top.map(h => (
              <li key={h.slug}>
                <Link href={`/ship/${h.slug}`} className="hub__chip">
                  <span className="hub__chip-name">{h.name}</span>
                  <span className="hub__chip-count">{h.work_count.toLocaleString()}</span>
                </Link>
              </li>
            ))}
          </ul>

          <h2 className="hub__heading">All pairings A–Z</h2>
          {/* Jump links: the full list is thousands of entries, and a reader
              looking for one pairing should not have to scroll past every other. */}
          <p className="hub__alphabet">
            {letters.map(l => <a key={l} href={`#letter-${l}`}>{l}</a>)}
          </p>

          {letters.map(l => (
            <section key={l} className="hub__letter" id={`letter-${l}`}>
              <h3>{l}</h3>
              <ul className="hub__az">
                {groups.get(l)!.map(h => (
                  <li key={h.slug}>
                    <Link href={`/ship/${h.slug}`}>{h.name}</Link>{" "}
                    <span className="hub__az-count">{h.work_count.toLocaleString()}</span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </>
      )}
    </div>
  )
}
