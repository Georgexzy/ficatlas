import type { NextConfig } from "next"

// Backend host inside the Docker network. In compose, the service is named
// `backend` and listens on 8000. Override with INTERNAL_API_URL if running
// outside Docker (e.g. local dev with both processes on localhost).
const INTERNAL_API = process.env.INTERNAL_API_URL || "http://backend:8000"

// Security headers. Not needed while the only clients were on the tailnet;
// required once this answers to the internet.
//
// The reason a CSP earns its place here specifically: the reader renders chapter
// bodies with dangerouslySetInnerHTML, and those bodies come from four scraped
// sites and from user-uploaded EPUBs. html_sanitize.py allowlists them on the
// way out and is the primary defence — this is the second layer for the case
// where the sanitiser has a gap.
//
// script-src keeps 'unsafe-inline' because Next's hydration bootstrap is an
// inline script; removing it needs per-request nonces via middleware, which is
// a real change and not one to make blind. The directives below are the ones
// that do work without it: no plugins, no framing, no <base> rewriting, and
// form posts confined to this origin.
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
  // layout.tsx loads Playfair Display and DM Mono from Google Fonts, so the
  // stylesheet host has to be allowed or the whole typographic identity of the
  // site silently falls back to system fonts. (Self-hosting them would let this
  // drop back to 'self' and stop leaking a request to Google on every page —
  // worth doing, but it is a separate change.)
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  // Fic bodies legitimately reference remote images (AO3 and FFN both allow
  // them), so these are permitted over https — but data: and blob: are not, to
  // keep an injected payload from being self-contained.
  "img-src 'self' https: data:",
  "font-src 'self' https://fonts.gstatic.com",
  // The browser only ever talks to this origin: /api/* is a same-origin rewrite.
  "connect-src 'self'",
  "object-src 'none'",
  "frame-src 'none'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
]

// upgrade-insecure-requests rewrites every http:// subresource to https://.
// Behind Cloudflare that is correct and free. Over Tailscale it is fatal: the
// site is served as plain http on port 3000, so the browser rewrites every JS
// chunk to https://<tailnet-ip>:3000 and gets ERR_SSL_PROTOCOL_ERROR, because
// nothing is listening for TLS there. The page shell loads and not one script
// does — which is exactly how it looked: the PWA on the phone stopped working
// entirely while the server was serving 200s to everything.
//
// So it is opt-in, and set only in the production overlay where TLS is real.
if (process.env.FORCE_HTTPS === "true") CSP.push("upgrade-insecure-requests")

const CSP_HEADER = CSP.join("; ")

// The one Cache-Control for HTML a crawler is meant to read.
//
// One constant rather than the same 100 characters written out per route,
// because the thing this value is most likely to drift against is ITSELF: it
// already appears on the content routes, the hub index routes and the prose
// pages, and the moment two of those disagree nobody can say what the edge TTL
// of the site actually is.
//
// s-maxage=900 matches `export const revalidate = 900` on the story page so
// Next's own incremental cache and Cloudflare's edge expire together. It is
// also deliberately NOT a longer number for the hub index pages, which change
// once a day: a deploy swaps the hashed JS chunks the HTML points at and does
// not purge the edge (deploy/promote.sh has no purge step), so HTML sitting
// for an hour instead of fifteen minutes is an hour of handing out references
// to files that no longer exist. Keep stale revalidation brief as well, so old
// HTML does not keep referring to removed chunks after a deploy. Re-rendering
// a hub index 16 times a day
// costs nothing next to the 4.47s a cold render of it took when it was
// uncached.
//
// `max-age=0, must-revalidate` is the browser half and is load-bearing: a bad
// CSP once went sticky in phone caches, so no visitor may ever serve this
// HTML (and its headers) from their own cache without asking.
const DOC_CACHE_CONTROL =
  "public, max-age=0, must-revalidate, s-maxage=900, stale-while-revalidate=60"

const SECURITY_HEADERS = [
  { key: "Content-Security-Policy", value: CSP_HEADER },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  // Nothing here uses a camera, microphone or location, so refuse them outright
  // rather than leaving the decision to a future dependency.
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), interest-cohort=()" },
  // HSTS is unconditional, unlike upgrade-insecure-requests above, and the
  // difference is the failure mode rather than the intent. A browser IGNORES
  // this header when it arrives over plain http, so on the port-3000 origin it
  // is simply inert — where upgrade-insecure-requests was actively fatal there,
  // rewriting every script to a port serving no TLS. Over the Tailscale origin,
  // which terminates real TLS and is never reachable any other way, it does the
  // job it exists for.
  //
  // No includeSubDomains and no preload, both deliberately. includeSubDomains
  // would bind sibling names on the tailnet that this app knows nothing about,
  // and preload is a submission to a list baked into browser binaries that is
  // slow and awkward to reverse. 180 days is long enough to be worth having and
  // short enough to back out of by lowering it.
  { key: "Strict-Transport-Security", value: "max-age=15552000" },
]

const nextConfig: NextConfig = {
  // Keep the incremental cache in memory. The container is read_only, and Next's
  // default handler writes rendered ISR/force-static HTML into .next/server/app
  // on the image layer — see cache-handler.js for why the tmpfs and named-volume
  // fixes are both wrong. Without this, every /s/<code> render logged EROFS and
  // the page was re-rendered on every request.
  cacheHandler: require.resolve("./cache-handler.js"),
  // The custom handler IS the memory cache now, so Next's separate in-process
  // LRU would just hold a second copy of everything.
  cacheMaxMemorySize: 0,
  // Type errors fail the build.
  //
  // This was `ignoreBuildErrors: true`, and the cost of that was not theoretical:
  // `in_series` had been passed to searchStories for as long as the filter has
  // existed without being on SearchParams, and a dependency array referencing a
  // `const` declared further down the component — a temporal-dead-zone crash,
  // not a style question — compiled happily. Both were sitting in a build that
  // reported success. The tree typechecks clean, so this costs nothing today and
  // catches the next one.
  //
  // eslint stays off during builds: it is a separate, noisier decision, and
  // turning both on at once would make the first failure hard to attribute.
  eslint: { ignoreDuringBuilds: true },
  // Announcing the framework and version only helps someone matching us against
  // a CVE list.
  poweredByHeader: false,
  async headers() {
    return [
      { source: "/:path*", headers: SECURITY_HEADERS },
      // Documents must always revalidate.
      //
      // Next sends s-maxage for HTML, which governs shared caches only, leaving
      // browsers to cache heuristically off the ETag. That made a bad response
      // sticky: when the CSP briefly carried upgrade-insecure-requests, phones
      // kept serving that HTML — and its header — from their own cache long
      // after the server was fixed. A response carrying a CSP is a response
      // whose mistakes must be correctable by reloading.
      //
      // Costs nothing: the ETag still yields a 304 when nothing changed, so
      // this is a conditional request, not a re-download.
      // Public content pages: revalidate in the BROWSER, cache at the EDGE.
      //
      // The rule below exists because a bad CSP went sticky on phones, and that
      // property is preserved exactly -- `max-age=0, must-revalidate` still
      // means a browser never serves this from its own cache without asking.
      // What is added is `s-maxage`, which only shared caches read, so
      // Cloudflare may answer for 15 minutes without touching the origin.
      //
      // Measured before this: 7,065 of ~12,000 edge requests were /story/{id},
      // the hit ratio was 6.7%, and Cloudflare counted 1,032,799 requests in 30
      // days against 865 human pageviews.
      //
      // Measured AFTER, which corrects why: those 2,305 story requests hit 2,277
      // DISTINCT urls — a 1.2% repeat rate. Crawlers walk unique pages, so edge
      // caching absorbs almost none of that and the 6.7% was mostly arithmetic
      // rather than a fault. What this does buy is real but smaller: repeat
      // visitors, several search engines fetching the same page, and re-crawls
      // inside the short stale-while-revalidate window. Verified 36ms from the edge against 52ms
      // through to the origin.
      //
      // Safe to share between visitors because NOTHING under app/ calls
      // `cookies()` -- checked, zero matches -- so the server HTML is identical
      // for everyone and all reader state is fetched after hydration. The
      // Cloudflare rule additionally bypasses cache when the `sat` cookie is
      // present, which is the same guard the existing API rule uses.
      //
      // 900s matches `export const revalidate = 900` on the story page, so the
      // edge and Next's own incremental cache expire together rather than one
      // holding content the other has already replaced.
      //
      // `author` is in this list for the same reason the others are, and it
      // arrived late: the rule was written when the crawlable surface was
      // "story pages and the hubs they link to", and author hubs
      // (backend/author_hubs.py, 10,000 of them, `Allow: /author/` in
      // robots.txt) were built later as the same kind of page. Measured
      // 2026-10-21: `/author/astolat` answered `no-cache` + `cf-cache-status:
      // DYNAMIC` on every request while `/fandom/naruto` — same template, same
      // data shape, same crawler policy — answered s-maxage=900 and HIT. 236
      // `/author/*` requests reached the origin in 24h, every one of them a
      // render the edge could have answered.
      {
        source: "/:prefix(story|series|fandom|ship|author|s)/:path*",
        headers: [{ key: "Cache-Control", value: DOC_CACHE_CONTROL }],
      },
      // The index routes: the root, the five hub indexes robots.txt points at,
      // and the two prose pages a crawler reads to work out what this site is.
      //
      // These were the other half of the same gap — every one of them answered
      // `no-cache` + `DYNAMIC`, so each visit rendered HTML at a home server:
      //   /fandoms   1.6MB   /ships   2.2MB   /series   1.4MB   /authors  1.4MB
      // (uncompressed server HTML; 261KB / 374KB / 22KB / 351KB once brotli
      // applied), TTFB 0.26–0.41s warm. 955 requests for these paths reached
      // the origin in 24h — 637 to `/`, 236 to `/author/*`, 34 to `/fandoms`,
      // 48 to `/ships` — plus 437 to `/about`.
      //
      // Nothing here varies by reader for the same reason the content pages do
      // not: no `cookies()` anywhere under app/ (checked, zero matches), and
      // the numbers on `/` are fetched after hydration. Safe to share.
      //
      // Written as an exact-path rule rather than a prefix rule because
      // `/?q=…` shares the root's path with the landing page and `/?…` is the
      // infinite search space robots.txt exists to keep crawlers out of. The
      // Cloudflare half gates the same set on an empty query string for that
      // reason — see deploy/cloudflare_cache_rule.py.
      {
        source: "/",
        headers: [{ key: "Cache-Control", value: DOC_CACHE_CONTROL }],
      },
      {
        source: "/(fandoms|ships|tropes|authors|series|about|privacy)",
        headers: [{ key: "Cache-Control", value: DOC_CACHE_CONTROL }],
      },
      // The sitemap must survive the origin being unreachable.
      //
      // On 2 September Google Search Console stopped being able to read this
      // file, and the cause was not the file: Cloudflare logged 19,489 × 530
      // between 31 Aug and 3 Sep — the tunnel dropping, so the edge could not
      // reach the origin at all — and Googlebot's fetch was one of them. Google
      // backs off hard after that and does not retry for a long time, so a
      // couple of bad days cost weeks of not being crawled.
      //
      // The tunnel fault is fixed (`--protocol http2`, and 530s are 0 over the
      // last three days). This makes the sitemap resilient to the NEXT one:
      // with a week of stale-while-revalidate the edge keeps serving the last
      // good sitemap while the origin is away, and a crawler is never handed an
      // error for a file that had not actually changed.
      //
      // It was previously caught by the no-cache catch-all below, which is why
      // it answered `cf-cache-status: DYNAMIC` — every fetch went the whole way
      // to a home server for a document rebuilt from 11,196 rows.
      //
      // s-maxage=3600 against a hub rebuild that runs nightly: an hour is far
      // inside the window in which the answer cannot have changed.
      //
      // Two sources, because the flat file is now a sitemap INDEX and its
      // children live under /sitemaps/. Both need this: a crawler that can
      // reach the index and then gets an uncached, origin-bound fetch for each
      // of its seven children is worse off than it was with one flat file.
      {
        source: "/sitemap.xml",
        headers: [{
          key: "Cache-Control",
          value: "public, max-age=0, must-revalidate, s-maxage=3600, stale-while-revalidate=604800",
        }],
      },
      {
        source: "/sitemaps/:file",
        headers: [{
          key: "Cache-Control",
          value: "public, max-age=0, must-revalidate, s-maxage=3600, stale-while-revalidate=604800",
        }],
      },
      // NOTE: `sitemaps/` belongs in the exclusion list below as well as having
      // a rule above. The negative lookahead is what stops this catch-all from
      // winning, and a path that is only granted a rule above still gets
      // no-cache from here — which is how /sitemap.xml behaved before it was
      // named in both places.
      //
      // The same note now covers every route with an s-maxage above, and the
      // prefixes are written bare for that reason: `fandom` excludes
      // `/fandom/naruto` AND `/fandoms`, `series` excludes both, `author`
      // excludes both, so the index routes cannot be added above and left to be
      // caught here. `.+` rather than `.*` is what excludes the root: a
      // lookahead that matches nothing cannot reject an EMPTY remainder, so `/`
      // matched this rule and was the one path whose exclusion could not be
      // written as a prefix.
      //
      // `tests/check-cache-rules.py` checks this property directly — it takes
      // each s-maxage source's paths and asserts this pattern does not match
      // them — because the failure is invisible in review and silent in
      // production: the edge rule honours origin TTL, the origin says no-cache,
      // and nothing is ever cached by anybody.
      {
        source: "/((?!_next/static|_next/image|icon-|manifest.json|sitemap.xml|sitemaps/|story|series|fandom|ship|author|tropes|about|privacy|s/).+)",
        headers: [{ key: "Cache-Control", value: "no-cache, must-revalidate" }],
      },
    ]
  },
  // Proxy all /api/* requests from the frontend (port 3000) through to the
  // backend container. This means:
  //   - Browser only ever talks to one port (3000 — exposed via Tailscale).
  //   - Backend port 8000 doesn't need to be reachable from outside Docker.
  //   - No CORS (same origin), no mixed content, no env-var pinning.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${INTERNAL_API}/api/:path*` },
    ]
  },
}

export default nextConfig
