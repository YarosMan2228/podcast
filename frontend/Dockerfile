# Production image for the SPA: build with Vite, serve with Caddy.
# Caddy also reverse-proxies /api and /media to the Django `app` service and
# terminates TLS automatically when SITE_ADDRESS is a real domain.
FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /src/dist /srv
COPY Caddyfile /etc/caddy/Caddyfile
EXPOSE 80 443
