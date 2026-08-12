FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install
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

RUN npm run build && node gen-sw-precache.js
CMD ["npm", "run", "start"]
