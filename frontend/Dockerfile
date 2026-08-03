FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build && node gen-sw-precache.js
CMD ["npm", "run", "start"]
