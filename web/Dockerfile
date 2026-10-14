# PitchLens web: Next.js (standalone output). Build from the web/ directory:
#   docker build -t pitchlens-web --build-arg NEXT_PUBLIC_API_BASE=https://<engine>/api/v1 web
# NEXT_PUBLIC_API_BASE (and the optional NEXT_PUBLIC_STREAM_BASE) are compiled into the browser bundle, so
# they are build arguments.
FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM deps AS build
COPY . .
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000/api/v1
# Live streams from the Rust broadcast edge; empty means from the engine (NEXT_PUBLIC_API_BASE).
ARG NEXT_PUBLIC_STREAM_BASE=
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE \
    NEXT_PUBLIC_STREAM_BASE=$NEXT_PUBLIC_STREAM_BASE \
    NEXT_OUTPUT=standalone \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24-alpine AS run
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=8080 \
    HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
# 8080: unprivileged, and the port Azure Container Apps' placeholder image uses before the first deploy.
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/ >/dev/null || exit 1
CMD ["node", "server.js"]
