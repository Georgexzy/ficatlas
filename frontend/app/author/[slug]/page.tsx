import type { Metadata } from "next"
import Link from "next/link"
import SiteHeader from "../../SiteHeader"
import { notFound } from "next/navigation"
import { escapeJsonLd } from "@/lib/jsonLd"

const INTERNAL_API = process.env.INTERNAL_API_URL || "http://backend:8000"
const SITE = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000"

export const revalidate = 3600

// An author's page: what this name has posted on each archive, most-read
// first. See backend/author_hubs.py for which authors get one. Built the same
// way as the ship and fandom hubs, including the three-outcome lookup (a
// timeout must not become a cached 404) and a canonical that never depends on
// the lookup succeeding.

interface Work {
  id: string
  title: string
  author?: string
  summary?: string
  word_count?: number
  chapter_count?: number
  site?: string
  complete?: boolean
  fandoms?: string[]
}
interface SiteSection { site: string; works: Work[]; total?: number }
interface RelatedHub { kind: "fandom" | "ship"; slug: string; name: string; work_count: number }
interface Hub {
  slug: string
  name: string
  work_count: number
  works: Work[]
  sections?: SiteSection[]
  related?: RelatedHub[]
  profile_urls?: Record<string, string>
}

const SITE_LABELS: Record<string, string> = {
  ao3: "AO3",
  ffnet: "FanFiction.net",
  fictionalley: "FictionAlley",
}

type Lookup = { kind: "found"; hub: Hub } | { kind: "missing" } | { kind: "unavailable" }

async function lookupAuthor(slug: string): Promise<Lookup> {
  try {
    const r = await fetch(`${INTERNAL_API}/api/authors/${encodeURIComponent(slug)}`, {
      next: { revalidate },
      headers: { "x-internal-render": process.env.INTERNAL_RENDER_TOKEN || "" },
      signal: AbortSignal.timeout(10000),
    })
    if (r.status === 404) return { kind: "missing" }
    if (!r.ok) return { kind: "unavailable" }
    return { kind: "found", hub: await r.json() }
  } catch {
    return { kind: "unavailable" }
  }
}

function archivesOf(hub: Hub): string {
  const names = (hub.sections ?? []).filter(s => s.site).map(s => SITE_LABELS[s.site] ?? s.site)
  return names.length > 1
    ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`
    : names[0] ?? "the archives"
}

export async function generateMetadata(
  { params }: { params: Promise<{ slug: string }> },
): Promise<Metadata> {
  const { slug } = await params
  const canonical = `/author/${slug}`
  const r = await lookupAuthor(slug)
  if (r.kind !== "found") return { title: `${slug.replace(/-/g, " ")} — fanfiction`, alternates: { canonical } }
  const hub = r.hub
  const title = `${hub.name} — fanfiction on ${archivesOf(hub)}`
  const description =
    `${hub.work_count.toLocaleString()} works by ${hub.name} indexed from ${archivesOf(hub)}, `
    + `most-read first, with links to read each one on the archive that hosts it.`
  return {
    title, description, alternates: { canonical },
    openGraph: { title, description, type: "profile", siteName: "FicAtlas", images: "/og.png" },
    twitter: { card: "summary_large_image", title, description, images: "/og.png" },
  }
}

function fmt(n?: number): string | null {
  if (!n) return null
  return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n)
}

export default async function AuthorPage(
  { params }: { params: Promise<{ slug: string }> },
) {
  const { slug } = await params
  const found = await lookupAuthor(slug)
  if (found.kind === "missing") notFound()
  if (found.kind === "unavailable") throw new Error(`author page ${slug} could not be loaded`)
  const hub = found.hub
  const sections = (hub.sections ?? []).filter(s => s.works.length)
  const multi = sections.length > 1

  return (
    <div className="hub-page hub">
      <SiteHeader />
      <main id="main">
      <nav className="hub__crumbs" aria-label="Breadcrumb">
        <Link href="/">FicAtlas</Link>
      </nav>

      <script type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: escapeJsonLd([{
          "@context": "https://schema.org",
          "@type": "ProfilePage",
          name: `${hub.name} — fanfiction`,
          url: `${SITE}/author/${hub.slug}`,
          mainEntity: {
            "@type": "Person",
            name: hub.name,
          },
          hasPart: {
            "@type": "ItemList",
            itemListElement: sections.flatMap(s => s.works).map((w, i) => ({
              "@type": "ListItem", position: i + 1,
              item: { "@type": "CreativeWork", name: w.title, url: `${SITE}/story/${w.id}`,
                      author: { "@type": "Person", name: hub.name },
                      ...(w.word_count ? { wordCount: w.word_count } : {}) },
            })),
          },
        }]) }} />

      <p className="topic-hero__eyebrow">Author</p>
      <h1>{hub.name}</h1>
      <p className="hub__lede">
        {hub.work_count.toLocaleString()} works posted as <strong>{hub.name}</strong> on{" "}
        {archivesOf(hub)}, most-read first. Every title links to its page here and on to the
        archive that hosts it.
      </p>
      {/* Said plainly because it is true and a reader cannot tell otherwise:
          a username is unique within one archive, not across them. */}
      {multi && (
        <p className="hub__recs-note">
          Grouped by the name each archive shows. The same name on two archives is usually,
          but not always, the same person.
        </p>
      )}

      <p className="topic-hero__actions">
        <Link rel="nofollow" className="topic-btn topic-btn--primary"
          href={`/?q=${encodeURIComponent(`author:"${hub.name}"`)}`}>
          Search all {hub.work_count.toLocaleString()} works by {hub.name}
        </Link>
        {Object.entries(hub.profile_urls ?? {}).map(([site, url]) => (
          <a key={site} href={url} target="_blank" rel="noopener noreferrer"
            className="topic-btn">
            {SITE_LABELS[site] ?? site} ↗
          </a>
        ))}
      </p>

      {sections.map(section => (
        <section key={section.site}>
          <h2 className="hub__heading">
            On {SITE_LABELS[section.site] ?? section.site}
            {section.total ? <span className="hub__heading-more"> {section.total.toLocaleString()} works</span> : null}
          </h2>
          <ol className="hub__list">
            {section.works.map(w => (
              <li key={w.id} className="hub__item">
                <Link href={`/story/${w.id}`} className="hub__title">{w.title}</Link>
                <p className="hub__meta">
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

      {!!hub.related?.length && (
        <nav className="hub__related" aria-label="Their fandoms">
          <h2>Fandoms they write in</h2>
          <ul className="hub-chips">
            {hub.related.map(r => (
              <li key={`${r.kind}-${r.slug}`}>
                <Link href={`/${r.kind}/${r.slug}`}>{r.name}</Link>
                <span className="hub-chips-count">{r.work_count.toLocaleString()}</span>
              </li>
            ))}
          </ul>
        </nav>
      )}

      <p className="hub__foot">
        FicAtlas indexes what these archives publish and links you back to them.
        If this is you, you can <Link href="/permissions">choose how your work appears here</Link>,
        including not at all.
      </p>
      </main>
    </div>
  )
}
