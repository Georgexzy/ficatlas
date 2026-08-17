import type { Metadata } from "next"
import Link from "next/link"
import StoryClient from "./StoryClient"
import { escapeJsonLd } from "@/lib/jsonLd"

// A server wrapper so a story page can describe itself.
//
// Everything below the metadata is unchanged and still renders on the client;
// this exists purely so the page arrives with a title, a description and social
// tags rather than the site-wide defaults from layout.tsx.
//
// Before this, every one of ~19.9M story pages served the identical
// "FicAtlas — search AO3, FanFiction.net and FicAlley at once" and no author
// name at all. Two consequences, and the second was the one nobody had noticed:
//
//   * search engines had nothing to index, so `Allow: /story/` in robots.txt
//     permitted a crawl of pages that said nothing — the site was undiscoverable
//     by policy in one place and by accident in another;
//   * sharing a story link anywhere — Discord, Tumblr, a DM — produced a preview
//     with no title, author or summary. For a tool whose whole pitch is finding
//     work and sending you to it, a link that describes nothing is a real defect
//     regardless of what any crawler does.
//
// The fetch talks to the backend directly rather than through the /api rewrite,
// because this runs on the server where the rewrite does not apply.
const INTERNAL_API = process.env.INTERNAL_API_URL || "http://backend:8000"

// Long enough that a crawler or an unfurl does not hit the database on every
// request; short enough that an edited summary or a takedown is reflected
// quickly. Takedowns hide the text immediately regardless — this only governs
// the description of the listing.
export const revalidate = 900

interface HubLink { kind: "fandom" | "ship"; slug: string; name: string }

interface StoryMeta {
  title?: string
  author?: string
  summary?: string
  fandoms?: string[]
  word_count?: number
  chapter_count?: number
  is_hosted?: boolean
  source_restricted?: boolean
  delisted?: boolean
  hubs?: HubLink[]
}

async function fetchStory(id: string): Promise<StoryMeta | null> {
  try {
    const r = await fetch(`${INTERNAL_API}/api/stories/${id}`, {
      next: { revalidate },
      headers: { "x-internal-render": process.env.INTERNAL_RENDER_TOKEN || "" },
      signal: AbortSignal.timeout(8000),
    })
    if (!r.ok) return null
    return await r.json()
  } catch {
    // A metadata lookup must never be the reason a page fails to render. The
    // client component fetches the story itself and handles its own errors, so
    // falling back to the site defaults costs a good preview and nothing else.
    return null
  }
}

function summarise(s: StoryMeta): string {
  const bits: string[] = []
  if (s.author) bits.push(`by ${s.author}`)
  if (s.fandoms?.length) bits.push(s.fandoms.slice(0, 2).join(", "))
  if (s.word_count) bits.push(`${s.word_count.toLocaleString()} words`)
  const head = bits.join(" · ")
  // The author's own summary is the best description there is; the counts are a
  // fallback for the many works that have none.
  const body = (s.summary || "").replace(/\s+/g, " ").trim()
  const text = body ? `${head} — ${body}` : head
  return text.length > 300 ? text.slice(0, 297).trimEnd() + "…" : text
}

export async function generateMetadata(
  { params }: { params: Promise<{ id: string }> },
): Promise<Metadata> {
  const { id } = await params
  const story = await fetchStory(id)
  if (!story?.title) return {}          // falls back to the layout's defaults

  // A work its author has locked to registered users, or one that has been
  // delisted, is never handed to a search engine — whatever robots.txt permits
  // for story pages in general. robots.txt allowing a crawl is passive; a title
  // and description are what make a page worth indexing, and supplying those for
  // a work the author has taken out of public view would be us doing the
  // pushing. The page still renders; it just does not advertise itself.
  const hidden = !!(story.source_restricted || story.delisted)

  const description = summarise(story)
  const title = story.author
    ? `${story.title} — ${story.author}`
    : story.title

  return {
    title,
    description,
    // Its own URL. Without this the root layout's canonical applied and every
    // story page claimed to be a duplicate of the home page.
    alternates: { canonical: `/story/${id}` },
    robots: hidden ? { index: false, follow: true } : undefined,
    openGraph: {
      title,
      description,
      type: "article",
      siteName: "FicAtlas",
      images: "/og.png",
    },
    twitter: { card: "summary_large_image", title, description, images: "/og.png" },
  }
}

export default async function StoryPage(
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params
  const SITE = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000"
  const story = await fetchStory(id)

  // A story page is a CreativeWork: title, author, fandoms, counts, and above
  // all a summary — most of the index's stories have none, so the ones that do
  // get the credit for it here. The page's body is a client-render shell, so
  // this script is the only place the metadata lives in the HTML itself. The
  // works that are not ours (the majority) are described as CreativeWork too,
  // with a link to the hosting archive, because that is what the page is.
  const jsonLd = story ? {
    "@context": "https://schema.org",
    "@type": story.is_hosted ? "Book" : "CreativeWork",
    name: story.title,
    url: `${SITE}/story/${id}`,
    ...(story.author ? { author: { "@type": "Person", name: story.author } } : {}),
    ...(story.summary ? { description: story.summary.replace(/\s+/g, " ").trim().slice(0, 500) } : {}),
    ...(story.fandoms?.length ? { genre: story.fandoms } : {}),
    ...(story.word_count ? { wordCount: story.word_count } : {}),
    // Book's numberOfPages means printed pages, not chapters — so a chapter
    // count has no honest home in the Book schema. It still belongs in the
    // description of the node, and wordCount above carries the size signal.
    isPartOf: { "@type": "WebSite", name: "FicAtlas", url: SITE },
    publisher: { "@type": "Organization", name: "FicAtlas", url: SITE },
    inLanguage: "en",
    // The author's work stays on the archive they chose; this page only points
    // at it. isAccessibleForFree plus a link keeps the schema honest about what
    // FicAtlas actually is: a finding tool, not a mirror.
    isAccessibleForFree: true,
  } : null

  return (
    <>
      {jsonLd && (
        <script type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: escapeJsonLd(jsonLd) }} />
      )}
      <StoryClient />
      {/* Server-rendered links out of the story page, and the only ones on it.
          The body below renders on the client and links its fandoms and ships to
          `/?fandoms=…` and `/?relationships=…` — search URLs, which robots.txt
          blocks on purpose because the filter combination space is infinite. So
          a crawler followed hubs down into ~750k story pages and found nothing
          it was allowed to follow back out: the story pages absorbed the crawl
          and returned none of it, and the hubs got no link equity back from the
          pages they fed.

          Placed after StoryClient rather than inside it so it is in the HTML as
          it leaves the server, which is the entire point — the client tree is
          invisible to a crawler that does not run JS, and second-pass for one
          that does. It reads as a footer to the page, which is also where a
          reader wants "more like this" once they have decided about this work. */}
      {!!story?.hubs?.length && (
        <nav className="story-hubs" aria-label="Browse related">
          <span className="story-hubs__label">More like this</span>
          {story.hubs.map(h => (
            <Link key={`${h.kind}-${h.slug}`}
              href={h.kind === "fandom" ? `/fandom/${h.slug}` : `/ship/${h.slug}`}
              className={`story-hubs__link story-hubs__link--${h.kind}`}>
              {h.name}
            </Link>
          ))}
        </nav>
      )}
    </>
  )
}
