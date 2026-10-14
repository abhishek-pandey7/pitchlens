# PitchLens engine: FastAPI + the deterministic pipeline + agents + the MCP server.
# Build from the repository root (the image needs data/ for rosters and stored matches):
#   docker build -f engine/Dockerfile -t pitchlens-engine .
# The Rust core (engine/core): built once into an abi3 wheel, then only the wheel is copied over, so the
# runtime image carries no compiler.
FROM python:3.12-slim AS core
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable \
    && /root/.cargo/bin/cargo --version
ENV PATH=/root/.cargo/bin:$PATH
RUN pip install --no-cache-dir "maturin>=1.9,<2"
COPY engine/core /src/core
RUN cd /src/core/py && maturin build --release --out /wheels -i python3

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

# Dependencies first, in their own layer: they change far less often than the code.
COPY engine/pyproject.toml engine/README.md engine/
RUN python -c "import tomllib; d = tomllib.load(open('engine/pyproject.toml','rb')); print('\n'.join(d['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt
COPY --from=core /wheels /tmp/wheels
RUN pip install /tmp/wheels/pitchlens_core-*.whl && rm -rf /tmp/wheels

# The code and data keep the repository layout, so paths such as data/rosters resolve as in development.
COPY engine/pitchlens engine/pitchlens
COPY data data
RUN pip install --no-deps -e engine \
    && useradd --create-home --uid 10001 pitchlens \
    && mkdir -p data/.cache && chown -R pitchlens data/.cache  # tracking rebuilt for stored matches
USER pitchlens

# 8080: unprivileged, and the port Azure Container Apps' placeholder image uses before the first deploy.
EXPOSE 8080
# Container Apps terminates TLS and forwards X-Forwarded-*; the rate limiter keys clients by it.
ENV TRUST_PROXY_HEADERS=1
# The Rust core is installed: fail fast at startup if it ever goes missing from the image.
ENV PITCHLENS_CORE=rust
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/v1/health', timeout=4)"
CMD ["uvicorn", "pitchlens.api.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "20"]
