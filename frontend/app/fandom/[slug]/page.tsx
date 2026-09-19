import type { Metadata } from "next"
import Link from "next/link"
import SiteHeader from "../../SiteHeader"
import { notFound } from "next/navigation"
import { escapeJsonLd } from "@/lib/jsonLd"

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
const SITE = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000"

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
interface SiteSection { site: string; works: Work[]
  /** How many works this archive actually holds — NOT works.length,
   *  which is the per-archive cap. See hub_build.py. */
  total?: number }
interface Quality { tag: string; works: number }
interface RelatedHub {
  kind: "fandom" | "ship"
  slug: string
  name: string
  work_count: number
}

interface Hub {
  slug: string
  name: string
  work_count: number
  works: Work[]
  sections?: SiteSection[]
  related?: RelatedHub[]
  /** What works here tend to be — the refinement chips. */
  qualities?: Quality[]
}

const SITE_LABELS: Record<string, string> = {
  ao3: "AO3",
  ffnet: "FanFiction.net",
  fictionalley: "FictionAlley",
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

function searchHref(name: string, site?: string): string {
  const base = `/?fandoms=${encodeURIComponent(name)}`
  return site ? `${base}&sites=${site}` : base
}

export async function generateMetadata(
  { params }: { params: Promise<{ slug: string }> },
): Promise<Metadata> {
  const { slug } = await params
  const hub = await fetchHub(slug)
  if (!hub) return {}
  const description =
    `Browse ${hub.work_count.toLocaleString()} ${hub.name} fanworks indexed from `
    + `Archive of Our Own, FanFiction.net and FictionAlley. Search them all in one `
    + `place, then read on the archive that hosts them.`
  const title = `${hub.name} fanfiction`
  return {
    title,
    description,
    alternates: { canonical: `/fandom/${hub.slug}` },
    openGraph: { title, description, type: "website", siteName: "FicAtlas", images: "/og.png" },
    twitter: { card: "summary_large_image", title, description, images: "/og.png" },
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
      <main id="main">
      <nav className="hub__crumbs" aria-label="Breadcrumb">
        <Link href="/fandoms">All fandoms</Link>
      </nav>

      {/* Structured data for the two things this page is: a place in the
          site's hierarchy (BreadcrumbList) and a listing of works with their
          attributes (CollectionPage + ItemList). Google reads this without
          executing the page's JavaScript, which is the point — this is the
          route by which story entries get discovered at all. */}
      <script type="application/ld+json"
        dangerouslySetInnerHTML={{ __html:
          escapeJsonLd([
            {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            itemListElement: [
              { "@type": "ListItem", position: 1, name: "Fandom hub", item: `${SITE}/fandoms` },
              { "@type": "ListItem", position: 2, name: hub.name, item: `${SITE}/fandom/${hub.slug}` },
            ],
          },
          {
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            name: `${hub.name} fanfiction`,
            description:
              `${hub.work_count.toLocaleString()} ${hub.name} fanworks indexed from ` +
              `Archive of Our Own, FanFiction.net and FictionAlley.`,
            url: `${SITE}/fandom/${hub.slug}`,
            isPartOf: { "@type": "WebSite", name: "FicAtlas", url: SITE },
            mainEntity: {
              "@type": "ItemList",
              itemListElement: sections.flatMap(section => section.works).map((w, i) => ({
                "@type": "ListItem",
                position: i + 1,
                item: {
                  "@type": "CreativeWork",
                  name: w.title,
                  url: `${SITE}/story/${w.id}`,
                  ...(w.author ? { author: { "@type": "Person", name: w.author } } : {}),
                  ...(w.word_count ? { wordCount: w.word_count } : {}),
                  ...(w.summary ? { description: w.summary.slice(0, 500) } : {}),
                },
              })),
            },
          },
        ]) }} />

      <h1>{hub.name}</h1>
      <p className="hub__lede">
        {hub.work_count.toLocaleString()} works indexed from Archive of Our Own,
        FanFiction.net and FictionAlley.
      </p>

      {/* THE TOOL, ON THE PAGE — not a link to it.

          This page type is the site's front door: 77 of the 80 sessions Google
          sends in a month land on a hub rather than the home page. Nine per
          cent of them ever run a search. The page offered a LINK to the search
          box ("Search all N with filters →"), which is a navigation step
          between somebody who has just arrived and the only thing here they
          cannot get from the archive itself.

          A plain GET form, deliberately. It needs no JavaScript, a crawler
          sees a real form, and it submits to exactly the URL the search page
          already reads — the one carrying fandoms — so the reader lands on
          results scoped to this fandom rather than in an empty box.

          The placeholder is a sentence on purpose. Typed words that match
          nothing are now re-read against the vocabulary (see `Interpreted` in
          backend/api/search.py), so "a long one where they get together at the
          end" is a search this site can answer and most cannot. */}
      <form className="hub-find" action="/" method="get" role="search">
        <input type="hidden" name="fandoms" value={hub.name} />
        <label className="hub-find__label" htmlFor="hub-find-q">
          Search inside these {hub.work_count.toLocaleString()} works
        </label>
        <div className="hub-find__row">
          <input id="hub-find-q" name="q" type="search" className="hub-find__input"
            placeholder="time travel, complete, over 100k words" autoComplete="off" />
          <button type="submit" className="hub-find__go">Search</button>
        </div>
        <p className="hub-find__hint">
          Plain words work — describe the fic you want and we will read it as a search.
        </p>
      </form>

      {/* WHAT WORKS HERE ARE ACTUALLY LIKE.
          These were a fixed list — complete, 100k+, under 10k, recently
          updated — which is the same four suggestions on all 11,196 hubs and
          says nothing about any of them. They are this hub's OWN most common
          qualities now, sampled and filtered at build time, so Slow Burn
          appears on the pairings that have it and does not on the ones that
          do not. See hub_build.hub_qualities for what is excluded and why:
          the hub's own names and nicknames, the writing process, the artefact,
          and any word people write more often than they tag. */}
      {!!hub.qualities?.length && (
        <div className="hub-find__quick">
          <span className="hub-find__quick-label">Popular here</span>
          <ul>
            {hub.qualities.slice(0, 8).map(q => (
              <li key={q.tag}>
                <Link rel="nofollow"
                  href={`${searchHref(hub.name)}&tags=${encodeURIComponent(q.tag)}`}>
                  {q.tag}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Length and completion, which are true of every hub and so belong in
          the fixed row rather than up there with the ones that are not. */}
      <div className="hub-find__quick hub-find__quick--plain">
        <span className="hub-find__quick-label">Or narrow by</span>
        <ul>
          <li><Link rel="nofollow" href={`${searchHref(hub.name)}&status=complete`}>Complete</Link></li>
          <li><Link rel="nofollow" href={`${searchHref(hub.name)}&word_count_min=100000`}>100k+ words</Link></li>
          <li><Link rel="nofollow" href={`${searchHref(hub.name)}&word_count_max=10000`}>Under 10k</Link></li>
          <li><Link rel="nofollow" href={`${searchHref(hub.name)}&sort=updated_desc`}>Recently updated</Link></li>
        </ul>
      </div>

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
            <Link rel="nofollow" href={section.site
              ? `/?fandoms=${encodeURIComponent(hub.name)}&sites=${section.site}`
              : `/?fandoms=${encodeURIComponent(hub.name)}`}>
              {section.site
                ? `Most popular on ${SITE_LABELS[section.site] ?? section.site}`
                /* The pre-rebuild fallback has no archive to name. */
                : `Most popular ${hub.name} works`}
              {/* The archive's REAL size, not the length of the list below
                  it. They are wildly different numbers — AO3 holds 51,894 of
                  the 53,265 Drarry works and shows 39 of them — and the big
                  one is what tells a reader whether this archive is where
                  their fic lives. 0 on a hub built before the count existed,
                  and then simply not shown. */}
              <span className="hub__heading-more" aria-hidden="true">
                {section.total ? `${section.total.toLocaleString()} · see all →` : "see all →"}
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
                      by <Link rel="nofollow" href={`/?author=${encodeURIComponent(w.author)}`}>{w.author}</Link>
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
              <Link rel="nofollow" href={`/?fandoms=${encodeURIComponent(hub.name)}&sites=${section.site}`}>
                Search every {hub.name} work on {SITE_LABELS[section.site] ?? section.site} →
              </Link>
            </p>
          )}
        </section>
      ))}

      {/* The site's only lateral link, and the reason it exists is measured:
          Googlebot crawls this site 119 times a day and had reached 90 DISTINCT
          hubs in the whole retained log, because `/ships` linked every hub, every
          hub linked 100 story pages, and no hub linked to any other. A crawler
          arriving on one pairing from a search result had nowhere to go but back
          out. 56% of all referred visits land on a ship hub, so these are also
          the pages whose authority is worth passing on.

          Server-rendered and outside any client component, for the same reason
          `.story-hubs` is on the story page: a link that needs JavaScript is not
          a link a crawler follows. */}
      {!!hub.related?.length && (
        <nav className="hub__related" aria-label="Related pages">
          <h2>Popular pairings in this fandom</h2>
          <ul>
            {hub.related.map(r => (
              <li key={`${r.kind}-${r.slug}`}>
                <Link href={`/${r.kind}/${r.slug}`}>{r.name}</Link>
                <span className="hub__related-count">
                  {r.work_count.toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        </nav>
      )}

      <p className="hub__foot">
        FicAtlas indexes what these archives publish and links you back to them.
        Authors can <Link href="/permissions">manage how their work appears here</Link>.
      </p>
      </main>
    </div>
  )
}
