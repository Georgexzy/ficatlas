import type { Metadata } from "next"
import Link from "next/link"
import SiteHeader from "../../../SiteHeader"
import { notFound } from "next/navigation"
import { escapeJsonLd } from "@/lib/jsonLd"

// A trope hub: one fandom and one trope — "Harry Potter time travel" — the
// shape of what fans actually type into a search engine. See
// backend/topic_hubs.py for how the tropes are chosen and why: hub pages turn
// a visitor into a reader three times as well as the front door, and no page
// answered a fandom-plus-trope search before these.
//
// Server-rendered like the fandom hub beside it, for the same reason: every
// link below must be in the HTML a crawler reads.
const INTERNAL_API = process.env.INTERNAL_API_URL || "http://backend:8000"
const SITE = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000"
export const revalidate = 3600

interface Work {
  id: string; title: string; author?: string; summary?: string
  word_count?: number; chapter_count?: number; site?: string; complete?: boolean
}
interface SiteSection { site: string; works: Work[]; total?: number }
interface TopicLink { slug: string; tag: string; work_count: number }
interface Topic {
  slug: string; name: string; work_count: number
  sections?: SiteSection[]; topics?: TopicLink[]
  fandom_slug: string; fandom_name: string; tag: string
}

const SITE_LABELS: Record<string, string> = {
  ao3: "AO3", ffnet: "FanFiction.net", fictionalley: "FictionAlley",
}

type Lookup = { kind: "found"; t: Topic } | { kind: "missing" } | { kind: "unavailable" }

// Same three outcomes as the fandom hub: only the API saying 404 earns a 404,
// which Next caches; a timeout must not become a cached "this never existed".
async function lookup(fandom: string, topic: string): Promise<Lookup> {
  try {
    const r = await fetch(
      `${INTERNAL_API}/api/hubs/${encodeURIComponent(fandom)}/t/${encodeURIComponent(topic)}`, {
      next: { revalidate },
      headers: { "x-internal-render": process.env.INTERNAL_RENDER_TOKEN || "" },
      signal: AbortSignal.timeout(10000),
    })
    if (r.status === 404) return { kind: "missing" }
    if (!r.ok) return { kind: "unavailable" }
    return { kind: "found", t: await r.json() }
  } catch {
    return { kind: "unavailable" }
  }
}

function fmt(n?: number): string | null {
  if (!n) return null
  return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n)
}

const searchHref = (t: Topic, site?: string) =>
  `/?fandoms=${encodeURIComponent(t.fandom_name)}&tags=${encodeURIComponent(t.tag)}`
  + (site ? `&sites=${site}` : "")

export async function generateMetadata(
  { params }: { params: Promise<{ slug: string; topic: string }> },
): Promise<Metadata> {
  const { slug, topic } = await params
  const r = await lookup(slug, topic)
  const canonical = `/fandom/${slug}/${topic}`
  if (r.kind !== "found") return { alternates: { canonical } }
  const t = r.t
  const title = `${t.fandom_name} ${t.tag} fanfiction`
  const description =
    `The most-read ${t.tag} fics in ${t.fandom_name}: ${t.work_count.toLocaleString()} `
    + `works from Archive of Our Own, FanFiction.net and FictionAlley, searchable `
    + `in one place and read on the archive that hosts them.`
  return {
    title, description, alternates: { canonical },
    openGraph: { title, description, type: "website", siteName: "FicAtlas", images: "/og.png" },
    twitter: { card: "summary_large_image", title, description, images: "/og.png" },
  }
}

export default async function TopicHub(
  { params }: { params: Promise<{ slug: string; topic: string }> },
) {
  const { slug, topic } = await params
  const r = await lookup(slug, topic)
  if (r.kind === "missing") notFound()
  if (r.kind === "unavailable") throw new Error(`topic ${slug}/${topic} could not be loaded`)
  const t = r.t
  const sections = t.sections ?? []
  const url = `${SITE}/fandom/${t.slug}`

  return (
    <div className="hub-page hub">
      <SiteHeader />
      <main id="main">
        <nav className="hub__crumbs" aria-label="Breadcrumb">
          <Link href="/fandoms">All fandoms</Link>
          <span aria-hidden="true"> › </span>
          <Link href={`/fandom/${t.fandom_slug}`}>{t.fandom_name}</Link>
        </nav>

        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: escapeJsonLd([
          {
            "@context": "https://schema.org", "@type": "BreadcrumbList",
            itemListElement: [
              { "@type": "ListItem", position: 1, name: "Fandoms", item: `${SITE}/fandoms` },
              { "@type": "ListItem", position: 2, name: t.fandom_name, item: `${SITE}/fandom/${t.fandom_slug}` },
              { "@type": "ListItem", position: 3, name: t.tag, item: url },
            ],
          },
          {
            "@context": "https://schema.org", "@type": "CollectionPage",
            name: `${t.fandom_name} ${t.tag} fanfiction`, url,
            isPartOf: { "@type": "WebSite", name: "FicAtlas", url: SITE },
            mainEntity: {
              "@type": "ItemList",
              itemListElement: sections.flatMap(s => s.works).map((w, i) => ({
                "@type": "ListItem", position: i + 1,
                item: { "@type": "CreativeWork", name: w.title, url: `${SITE}/story/${w.id}`,
                        ...(w.author ? { author: { "@type": "Person", name: w.author } } : {}) },
              })),
            },
          },
        ]) }} />

        <h1>{t.fandom_name}: {t.tag}</h1>
        <p className="hub__lede">
          {t.work_count.toLocaleString()} {t.fandom_name} works tagged {t.tag}, from
          Archive of Our Own, FanFiction.net and FictionAlley. The most-read of each
          archive are below; <Link rel="nofollow" href={searchHref(t)}>search all of them</Link> with
          your own filters.
        </p>

        {sections.length === 0 ? (
          <p className="hub__empty">Nothing to show here yet.</p>
        ) : sections.map(section => (
          <section key={section.site}>
            <h2 className="hub__heading hub__heading--link">
              <Link rel="nofollow" href={searchHref(t, section.site)}>
                {`Most read on ${SITE_LABELS[section.site] ?? section.site}`}
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
                    {w.author && <>by <Link rel="nofollow" href={`/?author=${encodeURIComponent(w.author)}`}>{w.author}</Link></>}
                    {fmt(w.word_count) && <span>{fmt(w.word_count)} words</span>}
                    {w.chapter_count ? <span>{w.chapter_count} ch</span> : null}
                    {w.complete && <span className="badge badge--complete">Complete</span>}
                  </p>
                  {w.summary && <p className="hub__summary">{w.summary}</p>}
                </li>
              ))}
            </ol>
          </section>
        ))}

        {/* Lateral links: the fandom's other tropes. Server-rendered so a
            crawler arriving on one can walk to the rest. */}
        {!!t.topics?.length && (
          <nav className="hub__related" aria-label="More in this fandom">
            <h2>More {t.fandom_name} tropes</h2>
            <ul className="hub-chips">
              {t.topics.map(x => (
                <li key={x.slug}>
                  <Link href={`/fandom/${x.slug}`}>{x.tag}</Link>
                  <span className="hub-chips-count">{x.work_count.toLocaleString()}</span>
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
