# PitchLens broadcast edge: a static musl binary on distroless, non-root, port 8080.
# Build from the edge/ directory:
#   docker build -t pitchlens-edge edge
#   docker run -p 8081:8080 -e ENGINE_URL=http://host.docker.internal:8000 pitchlens-edge
FROM rust:1-alpine AS build
# musl-dev and a C compiler: ring (rustls' crypto) compiles a little C and assembly.
RUN apk add --no-cache musl-dev gcc
WORKDIR /src
# Dependencies first, in their own layer, built against stub sources.
COPY Cargo.toml Cargo.lock ./
COPY loadgen/Cargo.toml loadgen/
RUN mkdir -p src loadgen/src \
    && echo 'fn main() {}' > src/main.rs && touch src/lib.rs && echo 'fn main() {}' > loadgen/src/main.rs \
    && cargo build --release --locked -p pitchlens-edge \
    && rm -rf src loadgen/src target/release/deps/pitchlens_edge* target/release/deps/libpitchlens_edge* \
       target/release/.fingerprint/pitchlens-edge-*
COPY src src
COPY loadgen/src loadgen/src
# musl targets link statically, so the binary runs on an image with no libc at all.
RUN cargo build --release --locked -p pitchlens-edge && cp target/release/pitchlens-edge /pitchlens-edge

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /pitchlens-edge /pitchlens-edge
USER nonroot:nonroot
# 8080: unprivileged, and the port Azure Container Apps' placeholder image uses before the first deploy.
ENV PORT=8080 RUST_LOG=info
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 CMD ["/pitchlens-edge", "healthcheck"]
ENTRYPOINT ["/pitchlens-edge"]
