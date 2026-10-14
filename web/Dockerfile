# PitchLens web: Next.js (standalone output) plus the Rust kernel compiled to WebAssembly. Build from the
# repository root (the kernel lives in engine/core; web/Dockerfile.dockerignore keeps the context small):
#   docker build -f web/Dockerfile -t pitchlens-web --build-arg NEXT_PUBLIC_API_BASE=https://<engine>/api/v1 .
# NEXT_PUBLIC_API_BASE (and the optional NEXT_PUBLIC_STREAM_BASE) are compiled into the browser bundle, so
# they are build arguments.

# The kernel for the browser: the same Rust the engine runs, as static files served from /kernel/.
FROM rust:1-slim AS kernel
ARG WASM_BINDGEN=0.2.129
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && rustup target add wasm32-unknown-unknown \
    && curl -sSfL "https://github.com/wasm-bindgen/wasm-bindgen/releases/download/${WASM_BINDGEN}/wasm-bindgen-${WASM_BINDGEN}-x86_64-unknown-linux-musl.tar.gz" \
       | tar -xz -C /usr/local/bin --strip-components=1 "wasm-bindgen-${WASM_BINDGEN}-x86_64-unknown-linux-musl/wasm-bindgen"
COPY engine/core /src/core
RUN cd /src/core \
    && cargo build -p pitchlens-wasm --target wasm32-unknown-unknown --release \
    && wasm-bindgen target/wasm32-unknown-unknown/release/pitchlens_wasm.wasm --target web --out-dir /kernel \
       --out-name pitchlens_kernel --omit-default-module-path --no-typescript

FROM node:24-alpine AS deps
WORKDIR /app
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM deps AS build
COPY web/ .
COPY --from=kernel /kernel ./public/kernel
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
