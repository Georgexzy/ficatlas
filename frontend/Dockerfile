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
RUN npm run build && node gen-sw-precache.js
CMD ["npm", "run", "start"]
