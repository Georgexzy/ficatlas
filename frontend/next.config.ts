import type { NextConfig } from "next"

// Backend host inside the Docker network. In compose, the service is named
// `backend` and listens on 8000. Override with INTERNAL_API_URL if running
// outside Docker (e.g. local dev with both processes on localhost).
const INTERNAL_API = process.env.INTERNAL_API_URL || "http://backend:8000"

const nextConfig: NextConfig = {
  typescript: { ignoreBuildErrors: true },
  eslint: { ignoreDuringBuilds: true },
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
