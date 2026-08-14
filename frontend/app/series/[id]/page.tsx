import type { Metadata } from "next"
import SeriesClient from "./SeriesClient"
import { escapeJsonLd } from "@/lib/jsonLd"

// Server wrapper, same purpose and shape as the one over the story page: a
// series link shared anywhere used to preview as the site's generic blurb.
// See app/story/[id]/page.tsx for the reasoning in full.
const INTERNAL_API = process.env.INTERNAL_API_URL || "http://backend:8000"

export const revalidate = 900

export async function generateMetadata(
  { params }: { params: Promise<{ id: string }> },
): Promise<Metadata> {
  const { id } = await params
  try {
    const r = await fetch(`${INTERNAL_API}/api/stories/series/${id}`, {
      next: { revalidate },
      headers: { "x-internal-render": process.env.INTERNAL_RENDER_TOKEN || "" },
      signal: AbortSignal.timeout(8000),
    })
    if (!r.ok) return {}
    const s = await r.json()
    if (!s?.name) return {}

    // The series endpoint returns work_count/total_works and a `works` array —
    // there is no description field, so the counts are the whole description.
    const parts: string[] = []
    if (s.author) parts.push(`by ${s.author}`)
    const n = s.work_count ?? s.works?.length
    if (n) parts.push(`${n} work${n === 1 ? "" : "s"}`)
    if (s.total_words) parts.push(`${s.total_words.toLocaleString()} words`)
    const description = `Series \u00b7 ${parts.join(" \u00b7 ")}`.slice(0, 300)
    const title = s.author ? `${s.name} \u2014 ${s.author}` : s.name

    return {
      title,
      description,
      openGraph: { title, description, type: "article", siteName: "FicAtlas", images: "/og.png" },
      twitter: { card: "summary_large_image", title, description, images: "/og.png" },
    }
  } catch {
    // Never let a preview lookup break the page; the client fetches its own data.
    return {}
  }
}

export default async function SeriesPage(
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params
  const SITE = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000"

  let series: {
    name?: string
    author?: string
    work_count?: number
    total_words?: number
    works?: { id: string; title?: string }[]
  } | null = null
  try {
    const r = await fetch(`${INTERNAL_API}/api/stories/series/${id}`, {
      next: { revalidate },
      headers: { "x-internal-render": process.env.INTERNAL_RENDER_TOKEN || "" },
      signal: AbortSignal.timeout(8000),
    })
    if (r.ok) series = await r.json()
  } catch {
    series = null
  }

  const jsonLd = series?.name ? {
    "@context": "https://schema.org",
    "@type": "CreativeWorkSeries",
    name: series.name,
    url: `${SITE}/series/${id}`,
    ...(series.author ? { author: { "@type": "Person", name: series.author } } : {}),
    ...(series.work_count ? { numberOfEpisodes: series.work_count } : {}),
    ...(series.total_words ? { wordCount: series.total_words } : {}),
    ...(Array.isArray(series.works) && series.works.length ? {
      hasPart: series.works.map(w => ({
        "@type": "CreativeWork",
        name: w.title,
        url: `${SITE}/story/${w.id}`,
      })),
    } : {}),
    isPartOf: { "@type": "WebSite", name: "FicAtlas", url: SITE },
    publisher: { "@type": "Organization", name: "FicAtlas", url: SITE },
    inLanguage: "en",
  } : null

  return (
    <>
      {jsonLd && (
        <script type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: escapeJsonLd(jsonLd) }} />
      )}
      <SeriesClient params={params} />
    </>
  )
}
