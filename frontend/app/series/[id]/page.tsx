import type { Metadata } from "next"
import SeriesClient from "./SeriesClient"

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
      openGraph: { title, description, type: "article", siteName: "FicAtlas" },
      twitter: { card: "summary", title, description },
    }
  } catch {
    // Never let a preview lookup break the page; the client fetches its own data.
    return {}
  }
}

export default function SeriesPage({ params }: { params: Promise<{ id: string }> }) {
  return <SeriesClient params={params} />
}
