# Koi Fetch frontend image — multi-stage: build the React/Vite app with Node,
# then serve the static production build with Nginx (SPA fallback + /api and
# /ws proxied to the backend service).
#
# The React app itself is bootstrapped by a later task (package.json, Vite
# config, src/); this Dockerfile is authored for that app layout: manifest at
# ./frontend, Vite production output in ./dist (the Vite default outDir).

# --- Build stage ---
FROM node:22-alpine AS build

WORKDIR /app

# Install dependencies first for layer caching. `npm install` works with or
# without a committed package-lock.json (one is generated when absent).
COPY package.json* ./
RUN npm install

# Build the production bundle.
COPY . .
RUN npm run build

# --- Runtime stage ---
FROM nginx:1.27-alpine

# Nginx serves /usr/share/nginx/html on port 80 by default.
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
