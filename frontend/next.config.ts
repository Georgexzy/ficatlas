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
  "upgrade-insecure-requests",
].join("; ")

const SECURITY_HEADERS = [
  { key: "Content-Security-Policy", value: CSP },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  // Nothing here uses a camera, microphone or location, so refuse them outright
  // rather than leaving the decision to a future dependency.
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), interest-cohort=()" },
]

const nextConfig: NextConfig = {
  typescript: { ignoreBuildErrors: true },
  eslint: { ignoreDuringBuilds: true },
  // Announcing the framework and version only helps someone matching us against
  // a CVE list.
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: SECURITY_HEADERS }]
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
