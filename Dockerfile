# syntax=docker/dockerfile:1

# ---------------------------------------------------------------- builder ---
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies resolve from the lockfile in their own layer, so application
# edits do not invalidate the dependency cache.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ----------------------------------------------------------------- runtime ---
FROM python:3.12-slim AS runtime

# Runs as a non-root user with no shell. Nothing in this service needs to
# execute anything, so the image should not make it easy.
RUN groupadd --system --gid 1001 app \
 && useradd --system --uid 1001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app src/ ./src/
COPY --chown=app:app migrations/ ./migrations/
COPY --chown=app:app skills/ ./skills/

# HTTPS ends at the platform's proxy (Cloud Run, Fly.io, Railway, Caddy...),
# which forwards plain HTTP to this port. Trust its X-Forwarded-Proto/Host so
# anything the server builds from the request says https. Nothing here keys on
# the client address, so a forged header gains nothing; do not publish this
# port to the internet without that proxy in front.
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PC_PORT=8080 \
    FORWARDED_ALLOW_IPS="*"

USER app
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PC_PORT\",\"8080\")}/healthz', timeout=4).status==200 else 1)"]

CMD ["personal-context-mcp", "serve"]
