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
      // Measured on the live site before this: 7,065 of ~12,000 edge requests
      // were /story/{id}, the cache hit ratio was 6.7%, and Cloudflare counted
      // 1,032,799 requests in 30 days against 865 human pageviews. That gap is
      // crawlers walking ~750k story pages, and every one of them was travelling
      // to a home server. Crawl budget spent on latency is indexing not
      // happening, which is the opposite of the point of these pages.
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
      {
        source: "/:prefix(story|series|fandom|ship|s)/:path*",
        headers: [{
          key: "Cache-Control",
          value: "public, max-age=0, must-revalidate, s-maxage=900, stale-while-revalidate=86400",
        }],
      },
      {
        source: "/((?!_next/static|_next/image|icon-|manifest.json|story/|series/|fandom/|ship/|s/).*)",
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
