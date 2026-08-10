import type { Metadata } from "next"
import Link from "next/link"
import SiteHeader from "../../SiteHeader"
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
interface SiteSection { site: string; works: Work[] }
interface Hub {
  slug: string
  name: string
  work_count: number
  works: Work[]
  sections?: SiteSection[]
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
      headers: { "x-internal-render": process.env.INTERNAL_RENDER_TOKEN || "" },
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

  // Fall back to one merged section if an older hub row has no per-site data,
  // so a page still renders between a deploy and the next rebuild.
  const sections: SiteSection[] = hub.sections?.length
    ? hub.sections
    : hub.works.length ? [{ site: "", works: hub.works }] : []

  return (
    <div className="hub-page hub">
      {/* Not current="browse": that suppresses the link, and from a single
          fandom the index of all of them is somewhere you actually want to go. */}
      <SiteHeader />
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

      {/* One section per archive rather than one merged list.
          A single ranking put AO3 in every slot on every hub, because kudos is
          the popularity column and it exists on 239,588 AO3 rows against 1,470
          of FanFiction.net's 6.57M. Ranking across archives was not meaningful
          either way — an AO3 kudos and a FanFiction.net favourite are different
          units — so each archive gets its own list, and the heading says which
          archive you are looking at. */}
      {sections.length === 0 ? (
        <p className="hub__empty">Nothing to show here yet.</p>
      ) : sections.map(section => (
        <section key={section.site}>
          {/* The heading is the link. Someone reading "Most popular on AO3" and
              wanting more of it should not have to find their way back up to
              the lede — the obvious thing to click is the thing they are
              already looking at, and it lands on this fandom filtered to that
              archive, in the real search UI with all the filters. */}
          <h2 className="hub__heading hub__heading--link">
            <Link href={section.site
              ? `/?fandoms=${encodeURIComponent(hub.name)}&sites=${section.site}`
              : `/?fandoms=${encodeURIComponent(hub.name)}`}>
              {section.site
                ? `Most popular on ${SITE_LABELS[section.site] ?? section.site}`
                /* The pre-rebuild fallback has no archive to name. */
                : `Most popular ${hub.name} works`}
              <span className="hub__heading-more" aria-hidden="true">
                see all →
              </span>
            </Link>
          </h2>
          <ol className="hub__list">
            {section.works.map(w => (
              <li key={w.id} className="hub__item">
                <Link href={`/story/${w.id}`} className="hub__title">{w.title}</Link>
                <p className="hub__meta">
                  {w.author && (
                    <>
                      by <Link href={`/?author=${encodeURIComponent(w.author)}`}>{w.author}</Link>
                    </>
                  )}
                  {fmt(w.word_count) && <span>{fmt(w.word_count)} words</span>}
                  {w.chapter_count ? <span>{w.chapter_count} ch</span> : null}
                  {w.complete && <span className="badge badge--complete">Complete</span>}
                </p>
                {w.summary && <p className="hub__summary">{w.summary}</p>}
              </li>
            ))}
          </ol>
          {section.site && (
            <p className="hub__section-more">
              <Link href={`/?fandoms=${encodeURIComponent(hub.name)}&sites=${section.site}`}>
                Search every {hub.name} work on {SITE_LABELS[section.site] ?? section.site} →
              </Link>
            </p>
          )}
        </section>
      ))}

      <p className="hub__foot">
        FicAtlas indexes what these archives publish and links you back to them.
        Authors can <Link href="/permissions">manage how their work appears here</Link>.
      </p>
    </div>
  )
}
