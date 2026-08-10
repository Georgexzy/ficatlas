import type { Metadata } from "next"
import Link from "next/link"
import { notFound } from "next/navigation"

// A fandom hub: the crawlable way into the index, and a genuinely useful page
// for someone arriving cold who has not used the search box yet.
//
// Server-rendered on purpose. The whole reason these exist is that no page on
// the site emitted a static href to /story/… — search results are built on the
// client and live behind `/?q=…` URLs that robots.txt blocks, so story pages
// had no inbound link a crawler could follow. A hub that rendered its list on
// the client would reproduce exactly that problem. Every link below is in the
// HTML as it leaves the server.
//
// See backend/fandom_hubs.py for why hubs are bounded rather than a sitemap of
// all 19.9M works.
const INTERNAL_API = process.env.INTERNAL_API_URL || "http://backend:8000"

// A day. Hubs are rebuilt offline and change slowly; the withdrawal checks that
// actually matter run at read time in the API, not off this cache.
export const revalidate = 86400

interface Work {
  id: string
  title: string
  author?: string
  summary?: string
  word_count?: number
  chapter_count?: number
  kudos?: number
  site?: string
  complete?: boolean
}
interface Hub {
  slug: string
  name: string
  work_count: number
  works: Work[]
}

const SITE_LABELS: Record<string, string> = {
  ao3: "AO3",
  ffnet: "FanFiction.net",
  fictionalley: "FicAlley",
}

async function fetchHub(slug: string): Promise<Hub | null> {
  try {
    const r = await fetch(`${INTERNAL_API}/api/hubs/${encodeURIComponent(slug)}`, {
      next: { revalidate },
      signal: AbortSignal.timeout(10000),
    })
    if (!r.ok) return null
    return await r.json()
  } catch {
    return null
  }
}

export async function generateMetadata(
  { params }: { params: Promise<{ slug: string }> },
): Promise<Metadata> {
  const { slug } = await params
  const hub = await fetchHub(slug)
  if (!hub) return {}
  const description =
    `Browse ${hub.work_count.toLocaleString()} ${hub.name} fanworks indexed from `
    + `Archive of Our Own, FanFiction.net and FicAlley. Search them all in one `
    + `place, then read on the archive that hosts them.`
  const title = `${hub.name} fanfiction`
  return {
    title,
    description,
    openGraph: { title, description, type: "website", siteName: "FicAtlas" },
    twitter: { card: "summary", title, description },
  }
}

function fmt(n?: number): string | null {
  if (!n) return null
  return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n)
}

export default async function FandomHub(
  { params }: { params: Promise<{ slug: string }> },
) {
  const { slug } = await params
  const hub = await fetchHub(slug)
  if (!hub) notFound()

  return (
    <div className="page-prose hub">
      <nav className="hub__crumbs" aria-label="Breadcrumb">
        <Link href="/fandoms">All fandoms</Link>
      </nav>

      <h1>{hub.name}</h1>
      <p className="hub__lede">
        {hub.work_count.toLocaleString()} works indexed from Archive of Our Own,
        FanFiction.net and FicAlley. {" "}
        {/* The search box is the real tool; this page is a doorway to it. The
            link carries the fandom through as a filter so it lands on results
            rather than an empty box. */}
        <Link href={`/?fandoms=${encodeURIComponent(hub.name)}`}>
          Search all {hub.work_count.toLocaleString()} with filters →
        </Link>
      </p>

      <h2 className="hub__heading">
        Most popular {hub.name} works
      </h2>

      {hub.works.length === 0 ? (
        <p className="hub__empty">Nothing to show here yet.</p>
      ) : (
        <ol className="hub__list">
          {hub.works.map(w => (
            <li key={w.id} className="hub__item">
              <Link href={`/story/${w.id}`} className="hub__title">{w.title}</Link>
              <p className="hub__meta">
                {w.author && (
                  <>
                    by <Link href={`/?author=${encodeURIComponent(w.author)}`}>{w.author}</Link>
                  </>
                )}
                {w.site && <span className={`badge badge--site-${w.site}`}>
                  {SITE_LABELS[w.site] ?? w.site}
                </span>}
                {fmt(w.word_count) && <span>{fmt(w.word_count)} words</span>}
                {w.chapter_count ? <span>{w.chapter_count} ch</span> : null}
                {w.complete && <span className="badge badge--complete">Complete</span>}
              </p>
              {w.summary && <p className="hub__summary">{w.summary}</p>}
            </li>
          ))}
        </ol>
      )}

      <p className="hub__foot">
        FicAtlas indexes what these archives publish and links you back to them.
        Authors can <Link href="/permissions">manage how their work appears here</Link>.
      </p>
    </div>
  )
}
