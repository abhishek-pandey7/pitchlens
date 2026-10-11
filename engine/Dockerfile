# PitchLens engine: FastAPI + the deterministic pipeline + agents + the MCP server.
# Build from the repository root (the image needs data/ for rosters and stored matches):
#   docker build -f engine/Dockerfile -t pitchlens-engine .
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

# The code and data keep the repository layout, so paths such as data/rosters resolve as in development.
COPY engine/pitchlens engine/pitchlens
COPY data data
RUN pip install --no-deps -e engine \
    && useradd --create-home --uid 10001 pitchlens
USER pitchlens

# 8080: unprivileged, and the port Azure Container Apps' placeholder image uses before the first deploy.
EXPOSE 8080
# Container Apps terminates TLS and forwards X-Forwarded-*; the rate limiter keys clients by it.
ENV TRUST_PROXY_HEADERS=1
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/v1/health', timeout=4)"
CMD ["uvicorn", "pitchlens.api.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "20"]
