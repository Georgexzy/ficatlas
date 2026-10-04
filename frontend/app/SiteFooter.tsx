import Link from "next/link"

// Rendered on every page from the root layout. Deliberately small: it is not
// selling anything, it exists so that a stranger can tell what this site is and
// an author can find the takedown route without being told where to look.
const INTERNAL_API = process.env.INTERNAL_API_URL || "http://backend:8000"

interface Landing { fandom?: { slug: string; name: string }[]; ship?: { slug: string; name: string }[] }

// The most-read pairings and fandoms, linked from every page's SERVER HTML.
//
// The home page carried no link to any hub in its HTML: its browse lists are
// fetched in the browser after load, which crawlers follow poorly if at all, so
// the 11,000 hubs were reachable only through /ships and /fandoms. Pairing hubs
// are the best entry page this site has (measured: 77% of their visitors arrive
// from search engines, 23% go on to read, against 6% from the home page), so the
// biggest of them get a link from every page.
//
// Never allowed to slow or break a page: skipped during `next build` (the
// backend does not exist in the build layer — see fandoms/page.tsx for the hang
// that caused), a 3s timeout, an hour's cache, and an empty list on any failure.
async function popularHubs(): Promise<Landing> {
  if (process.env.NEXT_PHASE === "phase-production-build") return {}
  try {
    const r = await fetch(`${INTERNAL_API}/api/hubs/landing`, {
      next: { revalidate: 3600 },
      headers: { "x-internal-render": process.env.INTERNAL_RENDER_TOKEN || "" },
      signal: AbortSignal.timeout(3000),
    })
    return r.ok ? await r.json() : {}
  } catch {
    return {}
  }
}

export default async function SiteFooter() {
  const hubs = await popularHubs()
  const ships = (hubs.ship ?? []).slice(0, 10)
  const fandoms = (hubs.fandom ?? []).slice(0, 10)
  return (
    <footer className="site-footer">
      {(ships.length > 0 || fandoms.length > 0) && (
        <nav className="site-footer__hubs" aria-label="Popular pairings and fandoms">
          {ships.length > 0 && (
            <p><span className="site-footer__hubs-k">Popular pairings</span>
              {ships.map(h => (
                <Link key={h.slug} prefetch={false} href={`/ship/${h.slug}`}>{h.name}</Link>
              ))}
            </p>
          )}
          {fandoms.length > 0 && (
            <p><span className="site-footer__hubs-k">Popular fandoms</span>
              {fandoms.map(h => (
                <Link key={h.slug} prefetch={false} href={`/fandom/${h.slug}`}>{h.name}</Link>
              ))}
            </p>
          )}
        </nav>
      )}
      <span>FicAtlas — search AO3, FanFiction.net and FictionAlley together</span>
      <span className="site-footer__sep">·</span>
      {/* The footer is on every page from the root layout, which makes this the
          one link that puts /fandoms — and through it every story page — inside
          the crawl graph. Without it the hubs are as unreachable as the story
          pages were, and the whole exercise achieves nothing.
          It earns its place for readers too: the search box needs you to know
          what you are looking for, and this is the answer to "what's in here?" */}
      {/* prefetch={false} on every link here, and it is not a micro-optimisation.
          Next prefetches a Link's RSC payload when it enters the viewport, and
          the footer is in the viewport on any short page — five requests per
          page, none of them abortable, against the ~6 connections a browser
          allows per host. On a connection that is dead but associated those
          five never settle, and they take the slots the READER needs: measured,
          a saved chapter opened on the first page load and on no page load
          after it. Nobody navigates to the privacy policy often enough to pay
          for that. */}
      <Link prefetch={false} href="/fandoms">Browse fandoms</Link>
      <span className="site-footer__sep">·</span>
      {/* Same job as the link above, for the other axis. /ships is reachable
          from /fandoms and vice versa, but a crawler that only ever sees a story
          page needs a root here too — and neither hub index should depend on the
          other being crawled first. */}
      <Link prefetch={false} href="/ships">Browse pairings</Link>
      <span className="site-footer__sep">·</span>
      <Link prefetch={false} href="/about">About</Link>
      <span className="site-footer__sep">·</span>
      <Link prefetch={false} href="/about#ai">AI policy</Link>
      <span className="site-footer__sep">·</span>
      {/* One author link, not two.
          "Remove my story" and "I'm an author" sat side by side pointing at the
          two halves of the same job, which is the duplication this footer was
          meant to avoid rather than create.
          Merged toward removal, not away from it. This link exists because an
          author must be able to find the takedown route without being told where
          to look, and that is the urgent case — so the label leads with
          "Remove", and the page it lands on opens with removal as a button
          before it mentions anything else. */}
      <Link prefetch={false} href="/permissions">Remove or manage my work</Link>
      <span className="site-footer__sep">·</span>
      {/* /privacy had NO inbound link from anywhere on the site — it was
          written and never hung off anything, so the only way to reach it was to
          know the address. A privacy page nobody can find is the same half-promise
          the data controls were built to close ("it never leaves your device" is
          only half a promise; the other half is being able to see it). It also has
          to be reachable for the Google sign-in consent screen, which names it.

          Next to the crawler policy rather than up beside About, because these
          two are the same kind of thing: what this site does with data, human and
          machine. */}
      <Link prefetch={false} href="/privacy">Privacy</Link>
      <span className="site-footer__sep">·</span>
      <a href="/robots.txt">Crawler policy</a>
      <span className="site-footer__sep">·</span>
      <a href="https://github.com/Georgexzy/ficatlas" target="_blank" rel="noopener noreferrer">Source</a>
    </footer>
  )
}
