FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
# Cap the build's heap.
#
# Node sizes its old-space against total system memory, so on a 15GB host the
# Next build helped itself to ~8GB — enough to push this workstation's desktop
# into swap and make it stutter while the image built. The build does not need
# it: 2GB is comfortable for this app and the ceiling only forces earlier GC.
ENV NODE_OPTIONS=--max-old-space-size=2048

# FORCE_HTTPS has to be a BUILD argument, not a runtime variable.
#
# next.config.ts uses it to decide whether the CSP carries
# upgrade-insecure-requests, and Next resolves headers() during `next build`,
# writing the result into .next/routes-manifest.json. `next start` then serves
# the manifest and never re-reads the config's headers. Setting FORCE_HTTPS in
# the container environment therefore does nothing at all — verified by finding
# the variable present in a running container while the header it controls was
# absent from the manifest.
#
# Left as a runtime variable this fails silently and in the safe direction, which
# is why it went unnoticed: the site works, it simply never upgrades insecure
# subresources. Build it in, and it is a property of the image — which is also
# correct, since an image built for the tunnel is not one you want served over
# plain http (see the notes in next.config.ts about what that breaks).
ARG FORCE_HTTPS=false
ENV FORCE_HTTPS=$FORCE_HTTPS

# INTERNAL_API_URL is build-time for exactly the same reason, and it fails much
# louder than FORCE_HTTPS did.
#
# next.config.ts rewrites /api/:path* to ${INTERNAL_API}, and Next resolves
# rewrites() during `next build` into .next/routes-manifest.json alongside the
# headers. The default is `http://backend:8000`, which is the dev stack's API
# container. On the public network there is no `backend` — the API containers are
# api-blue/api-green, deliberately named apart from the dev stack — so a public
# image built without this proxies every /api call to a host that does not
# resolve and answers 500 with EAI_AGAIN.
#
# That is the whole public API: search, suggestions, story pages. The site shell
# still returns 200, so it looks up while nothing on it works.
#
# It points at nginx's internal listener rather than a colour, so the frontend
# follows the active colour through a deploy instead of pinning to one.
ARG INTERNAL_API_URL=http://backend:8000
ENV INTERNAL_API_URL=$INTERNAL_API_URL

# NEXT_PUBLIC_SITE_URL is the third of these, and the most build-time of the
# three: Next's bundler substitutes NEXT_PUBLIC_* textually during the build, so
# the value is not read from the environment at all — it is compiled in. Setting
# it in the container is not merely ineffective, there is nothing left to read.
#
# It is the absolute origin the site describes itself with: sitemap <loc> entries
# and metadataBase. Unset, it falls back to http://localhost:3000 and the public
# sitemap advertises URLs on a host nobody else can reach, which Google discards
# as off-domain — the sitemap is served, parsed, and then thrown away whole.
ARG NEXT_PUBLIC_SITE_URL=http://localhost:3000
ENV NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL

RUN npm run build && node gen-sw-precache.js
CMD ["npm", "run", "start"]
