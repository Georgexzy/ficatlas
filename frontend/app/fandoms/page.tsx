import type { Metadata } from "next"
import Link from "next/link"

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
  description:
    "Every fandom indexed on FicAtlas, from Archive of Our Own, FanFiction.net "
    + "and FicAlley. Browse by fandom, then search within it.",
}

interface HubSummary { slug: string; name: string; work_count: number }

async function fetchHubs(): Promise<HubSummary[]> {
  try {
    const r = await fetch(`${INTERNAL_API}/api/hubs?limit=10000`, {
      next: { revalidate: CACHE_S },
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

  // Largest first is how the API returns them and how the page opens, because
  // that is what most visitors want. The alphabetical grouping below is for
  // finding a specific fandom, which is the other half of why anyone is here.
  const top = hubs.slice(0, 60)

  const groups = new Map<string, HubSummary[]>()
  for (const h of [...hubs].sort((a, b) => a.name.localeCompare(b.name))) {
    const first = h.name[0]?.toUpperCase() ?? "#"
    const key = /[A-Z]/.test(first) ? first : "#"
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(h)
  }
  const letters = [...groups.keys()].sort()

  return (
    <div className="page-prose hub">
      <h1>Browse fandoms</h1>
      <p className="hub__lede">
        {hubs.length.toLocaleString()} fandoms indexed across Archive of Our Own,
        FanFiction.net and FicAlley. Pick one to see its most popular works, or{" "}
        <Link href="/">search the whole index</Link> if you know what you want.
      </p>

      {hubs.length === 0 ? (
        <p className="hub__empty">
          Fandom pages aren’t built yet. <Link href="/">Search the index</Link> instead.
        </p>
      ) : (
        <>
          <h2 className="hub__heading">Biggest fandoms</h2>
          <ul className="hub__grid">
            {top.map(h => (
              <li key={h.slug}>
                <Link href={`/fandom/${h.slug}`} className="hub__chip">
                  <span className="hub__chip-name">{h.name}</span>
                  <span className="hub__chip-count">{h.work_count.toLocaleString()}</span>
                </Link>
              </li>
            ))}
          </ul>

          <h2 className="hub__heading">All fandoms A–Z</h2>
          {/* Jump links: the full list is thousands of entries, and a reader
              looking for one fandom should not have to scroll past every other. */}
          <p className="hub__alphabet">
            {letters.map(l => <a key={l} href={`#letter-${l}`}>{l}</a>)}
          </p>

          {letters.map(l => (
            <section key={l} className="hub__letter" id={`letter-${l}`}>
              <h3>{l}</h3>
              <ul className="hub__az">
                {groups.get(l)!.map(h => (
                  <li key={h.slug}>
                    <Link href={`/fandom/${h.slug}`}>{h.name}</Link>{" "}
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
