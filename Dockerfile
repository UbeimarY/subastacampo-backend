# syntax=docker/dockerfile:1

# ---------- Build stage: dependencies and generated code ----------
FROM python:3.11-slim AS build

# Pinned tool versions, copied from their official images (no unverified downloads).
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv
COPY --from=bufbuild/buf:1.50.0 /usr/local/bin/buf /usr/local/bin/buf

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Dependencies first: this layer stays cached until uv.lock changes.
# Prebuilt wheels only: no third-party build scripts run inside the image.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-build --no-install-project

# Contract and source code, then generate the RPC code from the contract.
COPY buf.yaml buf.gen.yaml ./
COPY proto ./proto
COPY src ./src
RUN buf generate

COPY alembic.ini ./
COPY migrations ./migrations

# ---------- Runtime stage: only what is needed to run ----------
FROM python:3.11-slim

# Never run as root inside the container.
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=build --chown=app:app /app /app

# The project runs from source: no build step for our own package either.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1
USER app
EXPOSE 8000

# Migrations run before the server starts (safe with a single instance).
CMD ["sh", "-c", "alembic upgrade head && uvicorn subastacampo.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]