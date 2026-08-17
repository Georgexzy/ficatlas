import { permanentRedirect, notFound } from "next/navigation"
import { decodeStoryId } from "@/lib/shortId"

// Short story links: /s/<22 chars> -> /story/<uuid>.
//
// A redirect, never a page. The canonical address of a work stays
// /story/<uuid>, so there is exactly one indexable URL per story and a short
// link adds no duplicate content — see the canonical note in layout.tsx for why
// that matters more here than it looks.
//
// Nothing is stored: the code IS the id, base64url instead of hex. See
// lib/shortId.ts.
//
// permanentRedirect (308), not redirect (307): the mapping is arithmetic and
// cannot change for a given work, so a crawler or a client is free to cache it
// and stop asking. Next's default redirect() is 307 — temporary — which would
// have every short link re-resolved forever.
export const dynamic = "force-static"

export default async function ShortLink(
  { params }: { params: Promise<{ code: string }> },
) {
  const { code } = await params
  const uuid = decodeStoryId(code)
  // A malformed code is a 404, not a redirect to a guessed id: decoding
  // something that is not one of ours would produce a plausible-looking UUID
  // for a work nobody asked for.
  if (!uuid) notFound()
  permanentRedirect(`/story/${uuid}`)
}
